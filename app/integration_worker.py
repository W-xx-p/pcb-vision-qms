from __future__ import annotations

import json
import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta
from os import getpid

import httpx

from app.config import (
    INTEGRATION_MAX_ATTEMPTS,
    INTEGRATION_RETRY_BASE_SECONDS,
    MES_WEBHOOK_TOKEN,
    MES_WEBHOOK_URL,
    PLC_HOST,
    PLC_PORT,
    PLC_RESULT_REGISTER,
    PLC_UNIT_ID,
)
from app.database import connect, init_database, utc_now

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcb_qms.integration")


def _deliver_mes(event: dict, payload: dict) -> str | None:
    if not MES_WEBHOOK_URL:
        return None
    headers = {"Idempotency-Key": event["idempotency_key"]}
    if MES_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {MES_WEBHOOK_TOKEN}"
    response = httpx.post(MES_WEBHOOK_URL, json={"event_type": event["event_type"], **payload}, headers=headers, timeout=10)
    response.raise_for_status()
    return response.headers.get("X-External-Reference") or str(response.json().get("id", ""))


def _deliver_plc(payload: dict) -> str | None:
    if not PLC_HOST or payload.get("event_type") not in (None, "inspection.completed"):
        return None
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as exc:
        raise RuntimeError("PLC已配置但未安装pymodbus") from exc
    value = 1 if payload.get("decision") == "anomaly" else 0
    client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=3)
    try:
        if not client.connect():
            raise ConnectionError("PLC连接失败")
        result = client.write_register(PLC_RESULT_REGISTER, value, device_id=PLC_UNIT_ID)
        if result.isError():
            raise RuntimeError(str(result))
    finally:
        client.close()
    return f"modbus://{PLC_HOST}:{PLC_PORT}/{PLC_RESULT_REGISTER}={value}"


def _claim_events(worker_id: str, limit: int = 50) -> list[dict]:
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=5)
    with connect() as db:
        db.execute(
            """UPDATE integration_events SET status = 'retrying', locked_at = NULL,
               locked_by = '' WHERE status = 'processing' AND locked_at < ?""",
            (stale.isoformat(),),
        )
        events = db.fetch_all(
            """SELECT * FROM integration_events
               WHERE status IN ('pending', 'retrying')
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
               ORDER BY id LIMIT ? FOR UPDATE SKIP LOCKED""",
            (now.isoformat(), limit),
        )
        for event in events:
            db.execute(
                """UPDATE integration_events SET status = 'processing',
                   locked_at = ?, locked_by = ? WHERE id = ?""",
                (now.isoformat(), worker_id, event["id"]),
            )
    return events


def dispatch_once(worker_id: str | None = None) -> int:
    simulator = os.getenv("PCB_QMS_INTEGRATION_SIMULATOR", "0") == "1"
    if not MES_WEBHOOK_URL and not PLC_HOST and not simulator:
        return 0
    worker_id = worker_id or f"{socket.gethostname()}-{getpid()}"
    events = _claim_events(worker_id)
    delivered = 0
    for event in events:
        payload = json.loads(event["payload_json"] or "{}")
        payload["event_type"] = event["event_type"]
        try:
            references = [value for value in (_deliver_mes(event, payload), _deliver_plc(payload)) if value]
            if not references and not simulator:
                with connect() as db:
                    db.execute(
                        """UPDATE integration_events SET status = 'pending',
                           locked_at = NULL, locked_by = '' WHERE id = ?""",
                        (event["id"],),
                    )
                continue
            if not references:
                references = [f"simulated:{event['id']}"]
            with connect() as db:
                db.execute(
                    """UPDATE integration_events SET status = 'delivered',
                       attempts = attempts + 1, external_reference = ?, delivered_at = ?,
                       locked_at = NULL, locked_by = '', last_error = '' WHERE id = ?""",
                    (";".join(references), utc_now(), event["id"]),
                )
            delivered += 1
        except Exception as exc:
            attempts = int(event["attempts"]) + 1
            status = "failed" if attempts >= INTEGRATION_MAX_ATTEMPTS else "retrying"
            delay = min(3600, INTEGRATION_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
            next_attempt = datetime.now(UTC) + timedelta(seconds=delay)
            with connect() as db:
                db.execute(
                    """UPDATE integration_events SET status = ?, attempts = ?,
                       next_attempt_at = ?, locked_at = NULL, locked_by = '', last_error = ?
                       WHERE id = ?""",
                    (
                        status,
                        attempts,
                        next_attempt.isoformat(),
                        f"{type(exc).__name__}: {exc}"[:1000],
                        event["id"],
                    ),
                )
            logger.exception("event delivery failed id=%s", event["id"])
    return delivered


def main() -> None:
    init_database()
    worker_id = f"{socket.gethostname()}-{getpid()}"
    while True:
        dispatch_once(worker_id)
        time.sleep(2)


if __name__ == "__main__":
    main()
