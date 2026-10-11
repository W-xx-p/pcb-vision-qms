from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike


def binary_auroc(labels: ArrayLike, scores: ArrayLike) -> float | None:
    """Return ROC-AUC using average ranks, including correct tie handling."""

    labels_array = np.asarray(labels, dtype=np.uint8)
    scores_array = np.asarray(scores, dtype=np.float64)
    if labels_array.size != scores_array.size or labels_array.size == 0:
        raise ValueError("标签与分数数量必须一致且不能为空")
    positives = int(labels_array.sum())
    negatives = int(labels_array.size - positives)
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(scores_array, kind="mergesort")
    sorted_scores = scores_array[order]
    sorted_labels = labels_array[order]
    # Compute average ranks per equal-score group without a Python loop.  Pixel
    # AUROC routinely contains tens of millions of values, where scalar rank
    # assignment would dominate the entire benchmark.
    starts = np.flatnonzero(
        np.concatenate(([True], sorted_scores[1:] != sorted_scores[:-1]))
    )
    ends = np.concatenate((starts[1:], [scores_array.size]))
    average_ranks = (starts + 1 + ends) / 2
    positive_counts = np.add.reduceat(sorted_labels.astype(np.int64), starts)
    positive_rank_sum = float(np.dot(average_ranks, positive_counts))
    auc = (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)
    return round(float(auc), 6)


def pixel_auroc(anomaly_maps: list[np.ndarray], masks: list[np.ndarray]) -> float | None:
    if len(anomaly_maps) != len(masks) or not anomaly_maps:
        return None
    labels = np.concatenate([(mask > 0).astype(np.uint8).ravel() for mask in masks])
    scores = np.concatenate([np.asarray(item, dtype=np.float32).ravel() for item in anomaly_maps])
    return binary_auroc(labels, scores)


def latency_percentiles(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    values = np.asarray(latencies_ms, dtype=np.float64)
    return {
        "mean_ms": round(float(values.mean()), 3),
        "p50_ms": round(float(np.percentile(values, 50)), 3),
        "p95_ms": round(float(np.percentile(values, 95)), 3),
    }
