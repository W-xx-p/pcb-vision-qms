from __future__ import annotations

import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir() -> Path:
    configured = os.getenv("PCB_QMS_DATA_DIR")
    candidates = (
        [Path(configured)]
        if configured
        else [BASE_DIR / "data", Path(tempfile.gettempdir()) / "pcb-qms-runtime"]
    )
    last_error: OSError | None = None
    for candidate in candidates:
        candidate = candidate.resolve()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # A unique probe avoids races when API and workers start together on
            # the same Docker volume. NamedTemporaryFile also removes stale
            # probes if a process exits normally.
            with tempfile.NamedTemporaryFile(
                prefix=".directory-write-probe-", dir=candidate, delete=True
            ):
                pass
            return candidate
        except OSError as exc:
            last_error = exc
    raise RuntimeError("没有可写的运行数据目录，请设置 PCB_QMS_DATA_DIR") from last_error


DATA_DIR = _resolve_data_dir()
MEDIA_DIR = DATA_DIR / "media"
MODEL_DIR = DATA_DIR / "models"
DATABASE_PATH = Path(os.getenv("PCB_QMS_DATABASE", DATA_DIR / "quality.db")).resolve()
DATABASE_URL = os.getenv(
    "PCB_QMS_DATABASE_URL",
    f"sqlite+pysqlite:///{DATABASE_PATH.as_posix()}",
).strip()
IMAGE_SIZE = int(os.getenv("PCB_QMS_IMAGE_SIZE", "256"))
MAX_IMAGE_PIXELS = int(os.getenv("PCB_QMS_MAX_IMAGE_PIXELS", "64000000"))
MAX_TILE_COUNT = int(os.getenv("PCB_QMS_MAX_TILE_COUNT", "512"))
REDIS_URL = os.getenv("PCB_QMS_REDIS_URL", "").strip()
MINIO_ENDPOINT = os.getenv("PCB_QMS_MINIO_ENDPOINT", "").strip()
MINIO_ACCESS_KEY = os.getenv("PCB_QMS_MINIO_ACCESS_KEY", "").strip()
MINIO_SECRET_KEY = os.getenv("PCB_QMS_MINIO_SECRET_KEY", "").strip()
MINIO_BUCKET = os.getenv("PCB_QMS_MINIO_BUCKET", "pcb-qms").strip()
MINIO_SECURE = os.getenv("PCB_QMS_MINIO_SECURE", "0") == "1"
ENVIRONMENT = os.getenv("PCB_QMS_ENV", "development").strip().lower()
RATE_LIMIT_PER_MINUTE = int(os.getenv("PCB_QMS_RATE_LIMIT_PER_MINUTE", "120"))
RATE_LIMIT_FAIL_OPEN = os.getenv("PCB_QMS_RATE_LIMIT_FAIL_OPEN", "0") == "1"
OIDC_ISSUER = os.getenv("PCB_QMS_OIDC_ISSUER", "").rstrip("/")
OIDC_AUDIENCE = os.getenv("PCB_QMS_OIDC_AUDIENCE", "pcb-qms")
OIDC_JWKS_URL = os.getenv("PCB_QMS_OIDC_JWKS_URL", "").strip()
OIDC_ROLE_CLAIM = os.getenv("PCB_QMS_OIDC_ROLE_CLAIM", "roles")
MODEL_GATE_MIN_SAMPLES = int(os.getenv("PCB_QMS_MODEL_GATE_MIN_SAMPLES", "20"))
MODEL_GATE_MIN_F1 = float(os.getenv("PCB_QMS_MODEL_GATE_MIN_F1", "0.85"))
MODEL_GATE_MIN_RECALL = float(os.getenv("PCB_QMS_MODEL_GATE_MIN_RECALL", "0.90"))
MODEL_GATE_MAX_FPR = float(os.getenv("PCB_QMS_MODEL_GATE_MAX_FPR", "0.10"))
DRIFT_MIN_SAMPLES = int(os.getenv("PCB_QMS_DRIFT_MIN_SAMPLES", "30"))
DRIFT_PSI_THRESHOLD = float(os.getenv("PCB_QMS_DRIFT_PSI_THRESHOLD", "0.20"))
MES_WEBHOOK_URL = os.getenv("PCB_QMS_MES_WEBHOOK_URL", "").strip()
MES_WEBHOOK_TOKEN = os.getenv("PCB_QMS_MES_WEBHOOK_TOKEN", "").strip()
PLC_HOST = os.getenv("PCB_QMS_PLC_HOST", "").strip()
PLC_PORT = int(os.getenv("PCB_QMS_PLC_PORT", "502"))
PLC_UNIT_ID = int(os.getenv("PCB_QMS_PLC_UNIT_ID", "1"))
PLC_RESULT_REGISTER = int(os.getenv("PCB_QMS_PLC_RESULT_REGISTER", "100"))
MODEL_SIGNING_KEY = os.getenv("PCB_QMS_MODEL_SIGNING_KEY", "").strip()
INTEGRATION_MAX_ATTEMPTS = int(os.getenv("PCB_QMS_INTEGRATION_MAX_ATTEMPTS", "10"))
INTEGRATION_RETRY_BASE_SECONDS = int(
    os.getenv("PCB_QMS_INTEGRATION_RETRY_BASE_SECONDS", "5")
)


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
