from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EvaluationMetrics:
    threshold: float
    sample_count: int
    normal_count: int
    anomaly_count: int
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    false_positive_rate: float

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_scores(samples: list[tuple[float, str]], threshold: float) -> EvaluationMetrics:
    tp = tn = fp = fn = 0
    for score, label in samples:
        prediction = "anomaly" if score >= threshold else "normal"
        if prediction == "anomaly" and label == "anomaly":
            tp += 1
        elif prediction == "normal" and label == "normal":
            tn += 1
        elif prediction == "anomaly":
            fp += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(samples) if samples else 0.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
    return EvaluationMetrics(
        # Keep the exact operating point. Rounding upward can turn a calibration
        # sample that sits on the threshold into a false negative when reused.
        threshold=float(threshold),
        sample_count=len(samples),
        normal_count=sum(label == "normal" for _, label in samples),
        anomaly_count=sum(label == "anomaly" for _, label in samples),
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        accuracy=round(accuracy, 4),
        false_positive_rate=round(false_positive_rate, 4),
    )


def find_best_threshold(samples: list[tuple[float, str]]) -> EvaluationMetrics:
    if not samples:
        raise ValueError("没有已复核样本")
    scores = sorted({score for score, _ in samples})
    candidates = [0.0, *scores, 1.0]
    metrics = [evaluate_scores(samples, threshold) for threshold in candidates]
    return max(metrics, key=lambda item: (item.f1, item.recall, -item.false_positive_rate))

