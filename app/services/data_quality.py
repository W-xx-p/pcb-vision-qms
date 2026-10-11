from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image


def perceptual_dhash(path: Path) -> str:
    """Return a compact difference hash used to catch near-duplicate images."""

    with Image.open(path) as image:
        pixels = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _visual_fingerprint(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(
            image.convert("L").resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32
        )


def validate_dataset_samples(samples: list[dict], near_duplicate_distance: int = 4) -> dict:
    """Detect exact, visual and batch leakage across dataset splits."""

    exact_groups: dict[str, list[dict]] = defaultdict(list)
    batch_splits: dict[str, set[str]] = defaultdict(set)
    prepared: list[dict] = []
    for sample in samples:
        path = Path(sample["path"])
        sha256 = sample.get("sha256")
        if not sha256:
            with path.open("rb") as stream:
                sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        record = {
            **sample,
            "sha256": sha256,
            "perceptual_hash": sample.get("perceptual_hash") or perceptual_dhash(path),
            "_fingerprint": _visual_fingerprint(path),
        }
        prepared.append(record)
        exact_groups[sha256].append(record)
        metadata = record.get("metadata") or {}
        batch = str(metadata.get("batch_id") or metadata.get("lot_number") or "").strip()
        if batch:
            batch_splits[batch].add(str(record["split"]))

    errors: list[dict] = []
    warnings: list[dict] = []
    for digest, group in exact_groups.items():
        if len(group) < 2:
            continue
        splits = sorted({str(item["split"]) for item in group})
        issue = {
            "type": "exact_duplicate",
            "sha256": digest,
            "splits": splits,
            "files": [str(item.get("filename") or item["path"]) for item in group],
        }
        (errors if len(splits) > 1 else warnings).append(issue)
    bands: dict[tuple[int, str], list[int]] = defaultdict(list)
    candidate_pairs: set[tuple[int, int]] = set()
    for index, sample in enumerate(prepared):
        digest = sample["perceptual_hash"]
        for band in range(8):
            key = (band, digest[band * 2:band * 2 + 2])
            for other_index in bands[key]:
                candidate_pairs.add((other_index, index))
            bands[key].append(index)
    for left_index, right_index in candidate_pairs:
        left, right = prepared[left_index], prepared[right_index]
        if left["sha256"] == right["sha256"] or left["split"] == right["split"]:
            continue
        distance = hamming_distance(left["perceptual_hash"], right["perceptual_hash"])
        visual_mae = float(np.abs(left["_fingerprint"] - right["_fingerprint"]).mean() / 255)
        if distance <= near_duplicate_distance and visual_mae <= 0.01:
            errors.append(
                {
                    "type": "near_duplicate_cross_split",
                    "distance": distance,
                    "visual_mae": round(visual_mae, 6),
                    "files": [
                        str(left.get("filename") or left["path"]),
                        str(right.get("filename") or right["path"]),
                    ],
                    "splits": [str(left["split"]), str(right["split"])],
                }
            )
    for batch, splits in batch_splits.items():
        if len(splits) > 1:
            errors.append({"type": "batch_cross_split", "batch_id": batch, "splits": sorted(splits)})
    return {
        "passed": not errors,
        "sample_count": len(prepared),
        "exact_duplicate_groups": sum(len(group) > 1 for group in exact_groups.values()),
        "errors": errors,
        "warnings": warnings,
        "near_duplicate_distance": near_duplicate_distance,
    }
