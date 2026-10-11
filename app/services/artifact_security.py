from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from app.config import MODEL_SIGNING_KEY

MANIFEST_NAME = "artifact.manifest.json"
SIGNATURE_NAME = "artifact.signature"


def create_artifact_manifest(artifact_dir: Path) -> tuple[dict[str, str], str]:
    """Create a deterministic checksum manifest and optional HMAC signature."""
    checksums: dict[str, str] = {}
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_file() and path.name not in {MANIFEST_NAME, SIGNATURE_NAME}:
            with path.open("rb") as stream:
                checksums[path.relative_to(artifact_dir).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    payload = json.dumps(checksums, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    (artifact_dir / MANIFEST_NAME).write_text(payload, encoding="utf-8")
    signature = ""
    if MODEL_SIGNING_KEY:
        signature = hmac.new(
            MODEL_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        (artifact_dir / SIGNATURE_NAME).write_text(signature, encoding="ascii")
    return checksums, signature


def verify_artifact_manifest(artifact_dir: Path) -> None:
    manifest = artifact_dir / MANIFEST_NAME
    if not manifest.exists():
        if MODEL_SIGNING_KEY:
            raise ValueError("模型缺少签名完整性清单")
        return  # Backward-compatible for unsigned development artifacts.
    payload = manifest.read_text(encoding="utf-8")
    if MODEL_SIGNING_KEY:
        signature_path = artifact_dir / SIGNATURE_NAME
        if not signature_path.exists():
            raise ValueError("模型缺少真实性签名")
        expected = hmac.new(
            MODEL_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature_path.read_text(encoding="ascii").strip()):
            raise ValueError("模型真实性签名校验失败")
    try:
        checksums = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("模型完整性清单格式无效") from exc
    for relative, expected in checksums.items():
        path = (artifact_dir / relative).resolve()
        if artifact_dir.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"模型文件不存在或路径无效: {relative}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if not hmac.compare_digest(actual, str(expected)):
            raise ValueError(f"模型完整性校验失败: {relative}")
