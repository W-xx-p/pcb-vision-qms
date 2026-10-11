from __future__ import annotations

import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import (
    DATABASE_URL,
    ENVIRONMENT,
    MINIO_SECRET_KEY,
    MODEL_SIGNING_KEY,
    OIDC_AUDIENCE,
    OIDC_ISSUER,
    OIDC_JWKS_URL,
    OIDC_ROLE_CLAIM,
    RATE_LIMIT_FAIL_OPEN,
    RATE_LIMIT_PER_MINUTE,
    REDIS_URL,
)
from app.services.infrastructure import redis_client

ROLES = {"admin", "engineer", "inspector", "reviewer", "auditor", "integration"}
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


def validate_security_config() -> None:
    if ENVIRONMENT != "production":
        return
    if not _configured_keys() and not OIDC_ISSUER:
        raise RuntimeError("生产模式必须配置PCB_QMS_API_KEYS或OIDC提供方")
    if not REDIS_URL:
        raise RuntimeError("生产模式必须配置Redis以提供跨实例限流")
    if len(MODEL_SIGNING_KEY) < 32:
        raise RuntimeError("生产模式必须配置至少32字符的PCB_QMS_MODEL_SIGNING_KEY")
    if os.getenv("PCB_QMS_SECURE_COOKIES", "0") != "1":
        raise RuntimeError("生产模式必须启用PCB_QMS_SECURE_COOKIES")
    weak_markers = ("change-this", "replace-with", "qmsadmin")
    combined = f"{DATABASE_URL} {MINIO_SECRET_KEY}".lower()
    if any(marker in combined for marker in weak_markers) or len(MINIO_SECRET_KEY) < 16:
        raise RuntimeError("生产模式检测到默认或弱数据库/MinIO凭据")


async def rate_limit_middleware(request: Request, call_next):
    if ENVIRONMENT != "production":
        return await call_next(request)
    identity = request.headers.get("x-api-key") or (request.client.host if request.client else "unknown")
    limit = 10 if request.url.path == "/api/auth/session" else RATE_LIMIT_PER_MINUTE
    # Hash credentials before using them in Redis keys; raw API keys must never
    # appear in operational stores or diagnostics.
    digest = hmac.digest(b"pcb-qms-rate-limit", identity.encode(), "sha256").hex()
    bucket = int(time.time() // 60)
    try:
        client = redis_client()
        if client is None:
            raise ConnectionError("Redis未配置")
        count, ttl = client.eval(
            _RATE_LIMIT_SCRIPT,
            1,
            f"pcb-qms:rate:{digest}:{bucket}",
            61,
        )
    except Exception:  # noqa: BLE001 - fail closed for every Redis transport failure
        if RATE_LIMIT_FAIL_OPEN:
            return await call_next(request)
        return JSONResponse(
            {"detail": "限流服务暂不可用"},
            status_code=503,
            headers={"Retry-After": "5"},
        )
    if int(count) > limit:
        retry_after = max(1, int(ttl))
        return JSONResponse(
            {"detail": "请求过于频繁，请稍后重试"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


def _configured_keys() -> dict[str, Principal]:
    raw = os.getenv("PCB_QMS_API_KEYS", "").strip()
    if raw:
        try:
            values = json.loads(raw)
            result = {
                key: Principal(str(item["name"]), str(item["role"]))
                for key, item in values.items()
                if str(item.get("role")) in ROLES
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("PCB_QMS_API_KEYS 必须是合法的角色密钥 JSON") from exc
        if not result:
            raise RuntimeError("PCB_QMS_API_KEYS 未包含有效角色密钥")
        return result
    legacy = os.getenv("PCB_QMS_API_KEY", "").strip()
    return {legacy: Principal("api-admin", "admin")} if legacy else {}


def current_principal(
    x_api_key: str | None = Header(default=None),
    qms_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    """Authenticate a role-scoped API key; local development defaults to admin."""

    configured = _configured_keys()
    if authorization and authorization.lower().startswith("bearer "):
        return _oidc_principal(authorization.split(" ", 1)[1])
    if not configured and not OIDC_ISSUER:
        return Principal("local-admin", "admin")
    presented = x_api_key or qms_session
    if presented:
        for secret, principal in configured.items():
            if hmac.compare_digest(secret, presented):
                return principal
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效或缺失的 API Key")


def _oidc_principal(token: str) -> Principal:
    if not OIDC_ISSUER:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OIDC未配置")
    try:
        import jwt
        if not OIDC_JWKS_URL:
            raise RuntimeError("缺少PCB_QMS_OIDC_JWKS_URL")
        key = jwt.PyJWKClient(OIDC_JWKS_URL).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=OIDC_AUDIENCE, issuer=OIDC_ISSUER)
    except Exception as exc:  # noqa: BLE001 - authentication must normalize provider failures
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"OIDC令牌校验失败: {type(exc).__name__}") from None
    roles = claims.get(OIDC_ROLE_CLAIM, [])
    if isinstance(roles, str):
        roles = [roles]
    if not roles and isinstance(claims.get("realm_access"), dict):
        roles = claims["realm_access"].get("roles", [])
    role = next((item for item in roles if item in ROLES), None)
    if not role:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "OIDC账户未分配QMS角色")
    return Principal(str(claims.get("preferred_username") or claims.get("sub")), role)


def require_roles(*allowed: str) -> Callable[..., str]:
    async def authorized(
        x_api_key: str | None = Header(default=None),
        qms_session: str | None = Cookie(default=None),
        authorization: str | None = Header(default=None),
    ) -> str:
        principal = current_principal(x_api_key, qms_session, authorization)
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色无权执行此操作")
        return principal.name

    return authorized


require_api_key = require_roles(*sorted(ROLES))
