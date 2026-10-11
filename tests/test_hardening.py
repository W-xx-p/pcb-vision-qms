from __future__ import annotations

from pathlib import Path

import pytest

from app.database import connect, fetch_one, utc_now
from app.services import artifact_security
from edge.station_agent import spool_size, validate_config


def test_artifact_signature_detects_tampering(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(artifact_security, "MODEL_SIGNING_KEY", "s" * 32)
    artifact = tmp_path / "model"
    artifact.mkdir()
    model_file = artifact / "weights.bin"
    model_file.write_bytes(b"trusted-model")
    _, signature = artifact_security.create_artifact_manifest(artifact)
    assert len(signature) == 64
    artifact_security.verify_artifact_manifest(artifact)
    model_file.write_bytes(b"tampered-model")
    with pytest.raises(ValueError, match="完整性校验失败"):
        artifact_security.verify_artifact_manifest(artifact)


def test_edge_config_recipe_and_spool_quota(tmp_path: Path):
    config = validate_config({
        "server": "https://qms.example.test",
        "api_key": "secret",
        "station_code": "AOI-01",
        "product_id": 1,
        "recipe": "board-b",
        "recipes": {"board-b": {"product_id": 2, "camera": "cam-b"}},
        "max_spool_bytes": 20,
    })
    assert config["product_id"] == 2
    assert config["camera"] == "cam-b"
    (tmp_path / "1.jpg").write_bytes(b"12345")
    assert spool_size(tmp_path) == 5


def test_model_rollback_state_machine(client):
    product = client.post(
        "/api/products",
        json={"code": "ROLLBACK-PCB", "name": "回滚测试板", "threshold": 0.2},
    ).json()
    with connect() as db:
        first = int(db.execute(
            """INSERT INTO model_versions(
                product_id, version, backend, status, gate_status, release_stage,
                is_active, parameters_json, metrics_json, gate_report_json, created_at
            ) VALUES (?, 1, 'reference', 'ready', 'passed', 'retired', FALSE,
                      '{}', '{}', '{}', ?) RETURNING id""",
            (product["id"], utc_now()),
        ).scalar_one())
        second = int(db.execute(
            """INSERT INTO model_versions(
                product_id, version, backend, status, gate_status, release_stage,
                is_active, supersedes_model_id, parameters_json, metrics_json,
                gate_report_json, created_at
            ) VALUES (?, 2, 'reference', 'ready', 'passed', 'active', TRUE, ?,
                      '{}', '{}', '{}', ?) RETURNING id""",
            (product["id"], first, utc_now()),
        ).scalar_one())
    response = client.post(
        f"/api/products/{product['id']}/models/rollback",
        json={"reason": "新版本现场误报率升高"},
    )
    assert response.status_code == 200
    assert response.json()["active_model_id"] == first
    assert fetch_one("SELECT release_stage FROM model_versions WHERE id = ?", (second,))["release_stage"] == "rolled_back"
    assert fetch_one("SELECT is_active FROM model_versions WHERE id = ?", (first,))["is_active"] in (True, 1)


def test_production_security_requires_distributed_controls(monkeypatch):
    from app import security

    monkeypatch.setattr(security, "ENVIRONMENT", "production")
    monkeypatch.setattr(security, "REDIS_URL", "")
    monkeypatch.setenv(
        "PCB_QMS_API_KEYS",
        '{"a-very-long-secret-key":{"name":"admin","role":"admin"}}',
    )
    with pytest.raises(RuntimeError, match="Redis"):
        security.validate_security_config()


def test_integration_worker_does_not_claim_without_downstream(monkeypatch):
    from app import integration_worker

    monkeypatch.setattr(integration_worker, "MES_WEBHOOK_URL", "")
    monkeypatch.setattr(integration_worker, "PLC_HOST", "")
    monkeypatch.delenv("PCB_QMS_INTEGRATION_SIMULATOR", raising=False)

    def unexpected_claim(*args, **kwargs):
        raise AssertionError("未配置下游时不应认领Outbox事件")

    monkeypatch.setattr(integration_worker, "_claim_events", unexpected_claim)
    assert integration_worker.dispatch_once("test-worker") == 0
