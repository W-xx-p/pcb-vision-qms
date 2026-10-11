from __future__ import annotations

import logging
import os
import time

from app.database import init_database
from app.services.drift import scan_all_products

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcb_qms.drift")


def main() -> None:
    init_database()
    interval = max(60, int(os.getenv("PCB_QMS_DRIFT_INTERVAL_SECONDS", "3600")))
    while True:
        try:
            logger.info("drift scan results=%s", scan_all_products())
        except Exception:
            logger.exception("drift scan failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
