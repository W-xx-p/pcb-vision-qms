from __future__ import annotations

import hashlib
import mimetypes
import time
from pathlib import Path

from app.config import (
    DATA_DIR,
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    MODEL_SIGNING_KEY,
    REDIS_URL,
)


def redis_client():
    if not REDIS_URL:
        return None
    from redis import Redis

    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=10,
        health_check_interval=30,
    )


def enqueue_training(job_id: int) -> bool:
    client = redis_client()
    if client is None:
        return False
    ensure_training_group(client)
    client.xadd("pcb-qms:training:stream", {"job_id": str(job_id), "attempt": "0"}, maxlen=10000, approximate=True)
    return True


def schedule_training_retry(job_id: int, attempt: int, delay_seconds: int) -> None:
    client = redis_client()
    if client is None:
        raise RuntimeError("Redis未配置")
    member = f"{job_id}:{attempt}"
    client.zadd("pcb-qms:training:delayed", {member: time.time() + delay_seconds})


def promote_training_retries(client=None, limit: int = 100) -> int:
    client = client or redis_client()
    if client is None:
        return 0
    members = client.zrangebyscore(
        "pcb-qms:training:delayed", "-inf", time.time(), start=0, num=limit
    )
    promoted = 0
    for member in members:
        if not client.zrem("pcb-qms:training:delayed", member):
            continue
        job_id, attempt = member.split(":", 1)
        client.xadd(
            "pcb-qms:training:stream",
            {"job_id": job_id, "attempt": attempt},
            maxlen=10000,
            approximate=True,
        )
        promoted += 1
    return promoted


def ensure_training_group(client=None) -> None:
    client = client or redis_client()
    if client is None:
        return
    try:
        client.xgroup_create("pcb-qms:training:stream", "training-workers", id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def minio_client():
    if not MINIO_ENDPOINT:
        return None
    from minio import Minio

    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def ensure_bucket() -> bool:
    client = minio_client()
    if client is None:
        return False
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)
    return True


def mirror_file(path: Path) -> str | None:
    client = minio_client()
    if client is None:
        return None
    ensure_bucket()
    resolved = path.resolve()
    try:
        key = resolved.relative_to(DATA_DIR.resolve()).as_posix()
    except ValueError:
        key = f"external/{resolved.name}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client.fput_object(MINIO_BUCKET, key, str(resolved), content_type=content_type)
    return key


def materialize_file(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate
    client = minio_client()
    if client is None:
        raise FileNotFoundError(f"对象存储未配置且本地文件不存在: {value}")
    key = str(value).replace("\\", "/").lstrip("/")
    suffix = Path(key).suffix
    cache = DATA_DIR / "cache" / f"{hashlib.sha256(key.encode()).hexdigest()}{suffix}"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        client.fget_object(MINIO_BUCKET, key, str(cache))
    return cache


def materialize_model_dir(value: str | Path, backend: str) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate
    client = minio_client()
    if client is None:
        raise FileNotFoundError(f"模型对象存储不可用: {value}")
    prefix = str(value).replace("\\", "/").strip("/")
    cache_dir = DATA_DIR / "cache" / "models" / hashlib.sha256(prefix.encode()).hexdigest()
    required = (
        ["normal_template.npy"]
        if backend == "reference"
        else [f"{backend}.xml", f"{backend}.bin", f"{backend}.sha256"]
    )
    required.append("artifact.manifest.json")
    if MODEL_SIGNING_KEY:
        required.append("artifact.signature")
    cache_dir.mkdir(parents=True, exist_ok=True)
    for filename in required:
        destination = cache_dir / filename
        if not destination.is_file():
            try:
                client.fget_object(MINIO_BUCKET, f"{prefix}/{filename}", str(destination))
            except Exception:
                # Older development artifacts predate the generic manifest.
                if filename == "artifact.manifest.json" and not MODEL_SIGNING_KEY:
                    continue
                raise
    return cache_dir


def delete_object(key: str) -> None:
    client = minio_client()
    if client is not None and not Path(key).is_absolute():
        client.remove_object(MINIO_BUCKET, key.replace("\\", "/"))


def infrastructure_status() -> dict:
    status = {
        "redis": {"configured": bool(REDIS_URL), "ready": False},
        "object_storage": {"configured": bool(MINIO_ENDPOINT), "ready": False},
    }
    if REDIS_URL:
        try:
            status["redis"]["ready"] = bool(redis_client().ping())
        except Exception as exc:  # noqa: BLE001 - readiness reports dependency failures
            status["redis"]["error"] = type(exc).__name__
    if MINIO_ENDPOINT:
        try:
            status["object_storage"]["ready"] = ensure_bucket()
        except Exception as exc:  # noqa: BLE001 - readiness reports dependency failures
            status["object_storage"]["error"] = type(exc).__name__
    return status
