from __future__ import annotations

import os
from pathlib import Path

from locust import HttpUser, between, task


class QmsUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.headers = {"X-API-Key": os.getenv("PCB_QMS_LOAD_API_KEY", "")}

    @task(5)
    def dashboard(self) -> None:
        self.client.get("/api/dashboard", headers=self.headers, name="dashboard")

    @task(3)
    def inspections(self) -> None:
        self.client.get("/api/inspections?limit=50", headers=self.headers, name="inspection-list")

    @task(1)
    def inference(self) -> None:
        image = os.getenv("PCB_QMS_LOAD_IMAGE")
        product = os.getenv("PCB_QMS_LOAD_PRODUCT_ID")
        if not image or not product or not Path(image).is_file():
            return
        with Path(image).open("rb") as stream:
            self.client.post(
                f"/api/products/{product}/inspect",
                headers=self.headers,
                files={"file": (Path(image).name, stream, "image/jpeg")},
                data={"station_code": os.getenv("PCB_QMS_LOAD_STATION", "")},
                name="inference",
            )
