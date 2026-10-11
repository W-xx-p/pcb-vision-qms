from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.database import enqueue_event, init_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcb_qms.alerts")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/alerts":
            self.send_error(404)
            return
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(body or b"{}")
            for alert in payload.get("alerts", []):
                fingerprint = alert.get("fingerprint", "unknown")
                enqueue_event("infrastructure.alert", fingerprint, alert)
            self.send_response(202)
            self.end_headers()
        except Exception:
            logger.exception("alert ingestion failed")
            self.send_error(500)

    def log_message(self, format: str, *args: object) -> None:
        logger.info(format, *args)


if __name__ == "__main__":
    init_database()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
