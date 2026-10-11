from __future__ import annotations

import json
import os

import numpy as np

from app.config import DRIFT_MIN_SAMPLES, DRIFT_PSI_THRESHOLD
from app.database import connect, enqueue_event, fetch_all, fetch_one, utc_now
from app.services.infrastructure import enqueue_training
from app.services.training import create_training_job


def _psi(baseline: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    edges = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    expected = np.histogram(baseline, bins=edges)[0] / len(baseline)
    actual = np.histogram(current, bins=edges)[0] / len(current)
    expected, actual = np.clip(expected, 1e-6, None), np.clip(actual, 1e-6, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def analyze_product_drift(product_id: int) -> dict:
    rows = fetch_all(
        """SELECT anomaly_score, quality_json, created_at, model_id
           FROM inspections WHERE product_id = ? ORDER BY created_at""",
        (product_id,),
    )
    if len(rows) < DRIFT_MIN_SAMPLES * 2:
        return {"ready": False, "reason": f"至少需要{DRIFT_MIN_SAMPLES * 2}条检测记录", "sample_count": len(rows)}
    values = np.asarray([float(row["anomaly_score"]) for row in rows])
    baseline, current = values[:DRIFT_MIN_SAMPLES], values[-DRIFT_MIN_SAMPLES:]
    psi = _psi(baseline, current)
    mean_shift = float(abs(current.mean() - baseline.mean()))
    quality_psi: dict[str, float] = {}
    for feature in ("brightness", "contrast", "sharpness"):
        feature_values = []
        for row in rows:
            try:
                value = json.loads(row.get("quality_json") or "{}").get(feature)
                if value is not None:
                    feature_values.append(float(value))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if len(feature_values) >= DRIFT_MIN_SAMPLES * 2:
            array = np.asarray(feature_values)
            quality_psi[feature] = round(
                _psi(array[:DRIFT_MIN_SAMPLES], array[-DRIFT_MIN_SAMPLES:]), 6
            )
    drifted_features = [
        name for name, value in quality_psi.items() if value >= DRIFT_PSI_THRESHOLD
    ]
    status = "drift" if psi >= DRIFT_PSI_THRESHOLD or drifted_features else "stable"
    active = fetch_one("SELECT id FROM model_versions WHERE product_id = ? AND is_active = TRUE", (product_id,))
    details = {
        "baseline_mean": round(float(baseline.mean()), 6),
        "current_mean": round(float(current.mean()), 6),
        "threshold": DRIFT_PSI_THRESHOLD,
        "method": "population_stability_index",
        "quality_feature_psi": quality_psi,
        "drifted_features": drifted_features,
    }
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO drift_reports(product_id, model_id, sample_count, psi, mean_shift, status, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (product_id, active["id"] if active else None, len(current), psi, mean_shift, status, json.dumps(details), utc_now()),
        )
        report_id = int(cursor.scalar_one())
        if status == "drift":
            enqueue_event("model.drift_detected", report_id, {"product_id": product_id, "psi": psi, **details}, db=db)
    retraining_job_id = None
    if status == "drift" and os.getenv("PCB_QMS_AUTO_RETRAIN", "0") == "1":
        backend = fetch_one("SELECT backend FROM model_versions WHERE product_id = ? AND is_active = TRUE", (product_id,))
        if backend:
            _, retraining_job_id = create_training_job(product_id, backend["backend"], {"trigger": "drift"})
            enqueue_training(retraining_job_id)
    return {"ready": True, "report_id": report_id, "status": status, "psi": round(psi, 6), "mean_shift": round(mean_shift, 6), "retraining_job_id": retraining_job_id, **details}


def scan_all_products() -> list[dict]:
    return [{"product_id": row["id"], **analyze_product_drift(row["id"])} for row in fetch_all("SELECT id FROM products WHERE enabled = TRUE")]
