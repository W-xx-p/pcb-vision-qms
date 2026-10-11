from __future__ import annotations

import logging
import threading
from collections import defaultdict
from time import perf_counter
from uuid import uuid4

from fastapi import Request

from app.database import fetch_one
from app.services.infrastructure import redis_client

logger = logging.getLogger("pcb_qms.requests")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.duration_seconds = 0.0
        self.status_counts: dict[int, int] = defaultdict(int)

    def record(self, status_code: int, duration_seconds: float) -> None:
        with self._lock:
            self.requests += 1
            self.duration_seconds += duration_seconds
            self.status_counts[status_code] += 1
            if status_code >= 500:
                self.errors += 1

    def prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP pcb_qms_http_requests_total Total HTTP requests.",
                "# TYPE pcb_qms_http_requests_total counter",
                f"pcb_qms_http_requests_total {self.requests}",
                "# HELP pcb_qms_http_errors_total Total HTTP 5xx responses.",
                "# TYPE pcb_qms_http_errors_total counter",
                f"pcb_qms_http_errors_total {self.errors}",
                "# HELP pcb_qms_http_request_duration_seconds_sum Cumulative request duration.",
                "# TYPE pcb_qms_http_request_duration_seconds_sum counter",
                f"pcb_qms_http_request_duration_seconds_sum {self.duration_seconds:.6f}",
            ]
            for status, count in sorted(self.status_counts.items()):
                lines.append(f'pcb_qms_http_responses_total{{status="{status}"}} {count}')
            try:
                business = fetch_one(
                    """SELECT COUNT(*) AS inspections,
                       SUM(CASE WHEN decision = 'anomaly' THEN 1 ELSE 0 END) AS anomalies,
                       AVG(latency_ms) AS avg_latency_ms FROM inspections"""
                ) or {}
                alerts = fetch_one("SELECT COUNT(*) AS count FROM quality_alerts WHERE status = 'open'") or {}
                outbox_failed = fetch_one(
                    "SELECT COUNT(*) AS count FROM integration_events WHERE status = 'failed'"
                ) or {}
                lines.extend([
                    "# TYPE pcb_qms_inspections_total gauge",
                    f"pcb_qms_inspections_total {int(business.get('inspections') or 0)}",
                    "# TYPE pcb_qms_anomalies_total gauge",
                    f"pcb_qms_anomalies_total {int(business.get('anomalies') or 0)}",
                    "# TYPE pcb_qms_inference_latency_milliseconds gauge",
                    f"pcb_qms_inference_latency_milliseconds {float(business.get('avg_latency_ms') or 0):.6f}",
                    "# TYPE pcb_qms_open_quality_alerts gauge",
                    f"pcb_qms_open_quality_alerts {int(alerts.get('count') or 0)}",
                    "# TYPE pcb_qms_outbox_failed_total gauge",
                    f"pcb_qms_outbox_failed_total {int(outbox_failed.get('count') or 0)}",
                ])
                redis = redis_client()
                if redis:
                    info = redis.xinfo_groups("pcb-qms:training:stream")
                    group = next((item for item in info if item.get("name") == "training-workers"), {})
                    lines.extend(["# TYPE pcb_qms_training_queue_lag gauge", f"pcb_qms_training_queue_lag {int(group.get('lag') or 0)}"])
                    lines.extend([
                        "# TYPE pcb_qms_training_delayed_total gauge",
                        f"pcb_qms_training_delayed_total {redis.zcard('pcb-qms:training:delayed')}",
                        "# TYPE pcb_qms_training_dead_letter_total gauge",
                        f"pcb_qms_training_dead_letter_total {redis.xlen('pcb-qms:training:dead-letter')}",
                    ])
            except Exception:
                logger.exception("business metrics collection failed")
            return "\n".join(lines) + "\n"


runtime_metrics = RuntimeMetrics()


async def request_metrics_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = perf_counter() - started
        runtime_metrics.record(500, duration)
        logger.exception(
            "request_id=%s method=%s path=%s unhandled_error duration_ms=%.2f",
            request_id, request.method, request.url.path, duration * 1000,
        )
        raise
    duration = perf_counter() - started
    runtime_metrics.record(response.status_code, duration)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self'; script-src 'self'; connect-src 'self'"
    )
    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration * 1000,
    )
    return response
