from __future__ import annotations

import logging
import signal
import socket
import time
from os import getpid

from app.database import fetch_one, init_database
from app.services.infrastructure import (
    ensure_training_group,
    promote_training_retries,
    redis_client,
    schedule_training_retry,
)
from app.services.training import run_training_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcb_qms.worker")
running = True


def stop_worker(*_: object) -> None:
    global running
    running = False


def main() -> None:
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    init_database()
    client = redis_client()
    if client is None:
        raise RuntimeError("Worker需要配置PCB_QMS_REDIS_URL")
    ensure_training_group(client)
    consumer = f"{socket.gethostname()}-{getpid()}"
    logger.info("training worker ready")
    while running:
        try:
            promote_training_retries(client)
            claimed = client.xautoclaim("pcb-qms:training:stream", "training-workers", consumer, 60000, "0-0", count=1)[1]
            messages = [("pcb-qms:training:stream", claimed)] if claimed else client.xreadgroup(
                "training-workers", consumer, {"pcb-qms:training:stream": ">"}, count=1, block=5000
            )
            for _, entries in messages:
                for message_id, fields in entries:
                    job_id = int(fields["job_id"])
                    attempt = int(fields.get("attempt", 0))
                    logger.info("processing training job %s attempt %s", job_id, attempt + 1)
                    run_training_job(job_id)
                    job = fetch_one("SELECT status, message FROM training_jobs WHERE id = ?", (job_id,))
                    if job and job["status"] == "completed":
                        client.xack("pcb-qms:training:stream", "training-workers", message_id)
                        client.xdel("pcb-qms:training:stream", message_id)
                    elif attempt < 2:
                        delay = 5 * (2 ** attempt)
                        schedule_training_retry(job_id, attempt + 1, delay)
                        client.xack("pcb-qms:training:stream", "training-workers", message_id)
                        client.xdel("pcb-qms:training:stream", message_id)
                    else:
                        client.xadd(
                            "pcb-qms:training:dead-letter",
                            {
                                "job_id": job_id,
                                "attempt": attempt + 1,
                                "error": (job or {}).get("message", "unknown"),
                                "failed_at": int(time.time()),
                            },
                            maxlen=1000,
                            approximate=True,
                        )
                        client.xack("pcb-qms:training:stream", "training-workers", message_id)
                        client.xdel("pcb-qms:training:stream", message_id)
        except Exception:
            logger.exception("worker queue connection failed; retrying")
            time.sleep(2)


if __name__ == "__main__":
    main()
