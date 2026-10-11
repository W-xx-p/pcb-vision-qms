from __future__ import annotations

import tempfile
from pathlib import Path

from app.config import (
    MODEL_GATE_MAX_FPR,
    MODEL_GATE_MIN_F1,
    MODEL_GATE_MIN_RECALL,
    MODEL_GATE_MIN_SAMPLES,
)
from app.database import fetch_all
from app.services.anomaly import get_backend
from app.services.evaluation import find_best_threshold
from app.services.infrastructure import materialize_file


def evaluate_candidate(product_id: int, backend: str, artifact_dir: Path) -> dict:
    """Evaluate a freshly trained artifact against human-reviewed holdout images.

    Scores are recomputed with the candidate artifact; historic scores from an older
    production model are never reused for a release decision.
    """
    rows = fetch_all(
        """
        SELECT image_path, reviewed_decision
        FROM inspections
        WHERE product_id = ? AND review_status = 'reviewed'
          AND reviewed_decision IN ('normal', 'anomaly')
        ORDER BY reviewed_at DESC LIMIT 500
        """,
        (product_id,),
    )
    scores: list[tuple[float, str]] = []
    errors: list[str] = []
    backend_impl = get_backend(backend)
    with tempfile.TemporaryDirectory(prefix="qms-gate-") as tmp:
        for index, row in enumerate(rows):
            try:
                result = backend_impl.inspect(
                    image_path=materialize_file(row["image_path"]),
                    reference_paths=[],
                    threshold=0.5,
                    heatmap_path=Path(tmp) / f"{index}.jpg",
                    artifact_path=artifact_dir,
                )
                scores.append((result.score, row["reviewed_decision"]))
            except Exception as exc:  # noqa: BLE001 - one bad holdout must not abort the report
                errors.append(f"{type(exc).__name__}: {exc}")

    criteria = {
        "min_samples": MODEL_GATE_MIN_SAMPLES,
        "min_f1": MODEL_GATE_MIN_F1,
        "min_recall": MODEL_GATE_MIN_RECALL,
        "max_false_positive_rate": MODEL_GATE_MAX_FPR,
    }
    if not scores:
        return {"passed": False, "reason": "没有可用的人工复核验证集", "criteria": criteria, "errors": errors[:10]}
    metrics = find_best_threshold(scores).as_dict()
    passed = (
        metrics["sample_count"] >= MODEL_GATE_MIN_SAMPLES
        and metrics["normal_count"] > 0
        and metrics["anomaly_count"] > 0
        and metrics["f1"] >= MODEL_GATE_MIN_F1
        and metrics["recall"] >= MODEL_GATE_MIN_RECALL
        and metrics["false_positive_rate"] <= MODEL_GATE_MAX_FPR
    )
    return {
        "passed": passed,
        "reason": "门禁通过，可由工程师发布" if passed else "指标或样本量未达到发布门禁",
        "criteria": criteria,
        "metrics": metrics,
        "inference_errors": errors[:10],
    }
