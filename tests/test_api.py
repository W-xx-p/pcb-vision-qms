from __future__ import annotations

import json
import shutil
from io import BytesIO

import pytest
from PIL import Image, ImageDraw


def image_bytes(defect: bool = False) -> bytes:
    image = Image.new("RGB", (256, 256), (20, 80, 55))
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, 220, 220), outline=(220, 190, 80), width=3)
    for x in range(55, 205, 35):
        draw.rectangle((x, 80, x + 20, 105), fill=(35, 38, 42))
    if defect:
        draw.rectangle((125, 125, 185, 185), fill=(240, 25, 25))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_product(client) -> dict:
    response = client.post(
        "/api/products",
        json={
            "code": "PCB-TEST",
            "name": "测试主控板",
            "description": "API test fixture",
            "threshold": 0.01,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/metrics").status_code == 200
    assert client.get("/styles.css").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_quality_workflow(client):
    product = create_product(client)
    product_id = product["id"]

    response = client.post(
        f"/api/products/{product_id}/references",
        files=[("files", ("normal.png", image_bytes(), "image/png"))],
    )
    assert response.status_code == 201

    training = client.post(
        f"/api/products/{product_id}/models/train",
        json={"backend": "reference"},
    )
    assert training.status_code == 202
    job = client.get(f"/api/training-jobs/{training.json()['job_id']}").json()
    assert job["status"] == "completed"

    response = client.post(
        f"/api/products/{product_id}/inspect",
        files={"file": ("defect.png", image_bytes(defect=True), "image/png")},
        data={"lot_number": "LOT-001", "serial_number": "SN-0001"},
    )
    assert response.status_code == 201
    inspection = response.json()
    assert inspection["decision"] == "anomaly"
    assert inspection["lot_number"] == "LOT-001"
    assert inspection["image_url"].startswith("/media/")

    review = client.patch(
        f"/api/inspections/{inspection['id']}/review",
        json={"decision": "anomaly", "note": "人工确认缺陷"},
    )
    assert review.status_code == 200
    assert review.json()["review_status"] == "reviewed"

    report = client.get("/api/reports/inspections.csv")
    assert report.status_code == 200
    assert "LOT-001" in report.text


def test_dataset_version_and_experiment_lineage(client):
    product = client.post(
        "/api/products",
        json={"code": "PCB-LINEAGE", "name": "血缘测试板", "threshold": 0.1},
    ).json()
    client.post(
        f"/api/products/{product['id']}/references",
        files=[("files", ("normal.png", image_bytes(), "image/png"))],
    )
    first = client.post(
        f"/api/products/{product['id']}/datasets/snapshot",
        json={"name": "golden-reference", "description": "受控正常样本"},
    )
    second = client.post(
        f"/api/products/{product['id']}/datasets/snapshot",
        json={"name": "golden-reference", "description": "受控正常样本"},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2
    assert first.json()["manifest_sha256"] == second.json()["manifest_sha256"]
    training = client.post(
        f"/api/products/{product['id']}/models/train",
        json={"backend": "reference", "dataset_version_id": first.json()["dataset_version_id"]},
    )
    assert training.status_code == 202
    experiments = client.get("/api/experiments").json()
    experiment = next(item for item in experiments if item["model_id"] == training.json()["model_id"])
    assert experiment["status"] == "completed"
    assert experiment["dataset_manifest_sha256"] == first.json()["manifest_sha256"]


def test_rejects_inspection_without_references(client):
    response = client.post(
        "/api/products",
        json={"code": "EMPTY-PCB", "name": "空样本产品", "threshold": 0.2},
    )
    product_id = response.json()["id"]
    response = client.post(
        f"/api/products/{product_id}/inspect",
        files={"file": ("board.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 409


def test_threshold_evaluation_and_calibration(client):
    response = client.post(
        "/api/products",
        json={"code": "PCB-CAL", "name": "阈值校准板", "threshold": 0.1},
    )
    product_id = response.json()["id"]
    client.post(
        f"/api/products/{product_id}/references",
        files=[("files", ("normal.png", image_bytes(), "image/png"))],
    )

    for index, defect in enumerate((False, False, True, True), start=1):
        inspection = client.post(
            f"/api/products/{product_id}/inspect",
            files={"file": (f"sample-{index}.png", image_bytes(defect=defect), "image/png")},
        ).json()
        label = "anomaly" if defect else "normal"
        client.patch(
            f"/api/inspections/{inspection['id']}/review",
            json={"decision": label, "note": "校准样本"},
        )

    evaluation = client.get(f"/api/products/{product_id}/evaluation")
    assert evaluation.status_code == 200
    assert evaluation.json()["sample_count"] == 4

    calibration = client.post(
        f"/api/products/{product_id}/calibrate-threshold",
        json={"apply_threshold": True},
    )
    assert calibration.status_code == 200
    assert calibration.json()["f1"] == 1.0
    assert calibration.json()["applied"] is True


def test_manufacturing_traceability_alert_and_spc(client):
    line = client.post(
        "/api/production-lines",
        json={"code": "LINE-T1", "name": "测试贴片线", "location": "F1"},
    )
    assert line.status_code == 201
    station = client.post(
        "/api/stations",
        json={
            "line_id": line.json()["id"],
            "code": "AOI-T1",
            "name": "测试AOI工位",
            "camera_identifier": "CAM-T1",
        },
    )
    assert station.status_code == 201
    heartbeat = client.post(
        f"/api/stations/{station.json()['id']}/heartbeat",
        json={"software_version": "edge-1.2.0"},
    )
    assert heartbeat.json()["status"] == "online"

    product = client.post(
        "/api/products",
        json={"code": "PCB-OPS", "name": "产线追溯板", "threshold": 0.01},
    ).json()
    client.post(
        f"/api/products/{product['id']}/references",
        files=[("files", ("normal.png", image_bytes(), "image/png"))],
    )
    rule = client.post(
        "/api/quality-rules",
        json={
            "name": "连续异常测试规则",
            "product_id": product["id"],
            "window_size": 5,
            "max_anomaly_rate": 1,
            "max_consecutive_anomalies": 2,
        },
    )
    assert rule.status_code == 201

    for index in range(2):
        response = client.post(
            f"/api/products/{product['id']}/inspect",
            files={"file": (f"defect-{index}.png", image_bytes(defect=True), "image/png")},
            data={
                "serial_number": f"OPS-SN-{index}",
                "lot_number": "OPS-LOT",
                "station_code": "AOI-T1",
                "shift": "白班",
            },
        )
        assert response.status_code == 201
        assert response.json()["defect_regions"]

    detail = client.get(f"/api/inspections/{response.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["station_code"] == "AOI-T1"
    feedback = client.post(
        f"/api/inspections/{response.json()['id']}/feedback",
        json={"category": "hard_sample", "note": "加入下一轮模型训练"},
    )
    assert feedback.status_code == 201

    duplicate = client.post(
        f"/api/products/{product['id']}/inspect",
        files={"file": ("duplicate.png", image_bytes(defect=True), "image/png")},
        data={"serial_number": "OPS-SN-1", "station_code": "AOI-T1"},
    )
    assert duplicate.status_code == 409

    alerts = client.get("/api/quality-alerts?status=open").json()
    alert = next(item for item in alerts if item["product_id"] == product["id"])
    assert alert["severity"] == "critical"
    acknowledged = client.patch(
        f"/api/quality-alerts/{alert['id']}/acknowledge",
        json={"note": "已通知工艺工程师"},
    )
    assert acknowledged.json()["status"] == "acknowledged"
    closed = client.patch(
        f"/api/quality-alerts/{alert['id']}/workflow",
        json={"status": "closed", "assigned_to": "process-engineer", "note": "已完成排查"},
    )
    assert closed.json()["status"] == "closed"

    updated_product = client.patch(
        f"/api/products/{product['id']}",
        json={"description": "已完成试产验证", "roi": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}},
    )
    assert updated_product.status_code == 200

    spc = client.get(f"/api/products/{product['id']}/spc?limit=100")
    assert spc.status_code == 200
    assert spc.json()["sample_count"] == 2
    assert len(client.get("/api/production-lines").json()) >= 1
    assert len(client.get("/api/stations").json()) >= 1

    events = client.get("/api/integration/events?status=pending").json()
    event = next(item for item in events if item["payload"]["product_id"] == product["id"])
    assert event["event_type"] == "inspection.completed"
    delivered = client.patch(
        f"/api/integration/events/{event['id']}/delivered",
        json={"external_reference": "MES-TEST-001"},
    )
    assert delivered.json()["status"] == "delivered"

    backup = client.post("/api/admin/backups")
    if backup.status_code == 503 and shutil.which("pg_dump") is None:
        assert "PostgreSQL备份失败" in backup.json()["detail"]
        return
    assert backup.status_code == 201
    assert backup.json()["bytes"] > 0
    assert client.get(backup.json()["download_url"]).status_code == 200


def test_role_based_access_control(client, monkeypatch):
    monkeypatch.setenv(
        "PCB_QMS_API_KEYS",
        json.dumps({"inspect-secret": {"name": "operator-01", "role": "inspector"}}),
    )
    payload = {"code": "RBAC-DENY", "name": "权限测试产品", "threshold": 0.2}
    assert client.get("/api/products").status_code == 401
    assert client.post("/api/products", json=payload).status_code == 401
    session = client.post(
        "/api/auth/session",
        headers={"X-API-Key": "inspect-secret"},
    )
    assert session.status_code == 200
    assert session.json()["role"] == "inspector"
    assert client.get("/api/auth/me").json()["actor"] == "operator-01"
    assert client.delete("/api/auth/session").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    forbidden = client.post(
        "/api/products",
        json=payload,
        headers={"X-API-Key": "inspect-secret"},
    )
    assert forbidden.status_code == 403


def test_inspection_outbox_transaction_rolls_back(client, monkeypatch):
    product = client.post(
        "/api/products",
        json={"code": "TX-ROLLBACK", "name": "事务回滚板", "threshold": 0.2},
    ).json()
    client.post(
        f"/api/products/{product['id']}/references",
        files=[("files", ("normal.png", image_bytes(), "image/png"))],
    )

    def fail_outbox(*args, **kwargs):
        raise RuntimeError("simulated outbox failure")

    monkeypatch.setattr("app.main.enqueue_event", fail_outbox)
    with pytest.raises(RuntimeError, match="simulated outbox failure"):
        client.post(
            f"/api/products/{product['id']}/inspect",
            files={"file": ("sample.png", image_bytes(), "image/png")},
        )
    assert client.get(f"/api/inspections?product_id={product['id']}").json() == []
