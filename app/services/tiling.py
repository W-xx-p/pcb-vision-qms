from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.config import MAX_TILE_COUNT
from app.services.anomaly import AnomalyResult, ReferenceDifferenceBackend


def tile_positions(length: int, tile_size: int, overlap: float) -> list[int]:
    if length <= tile_size:
        return [0]
    stride = max(1, int(tile_size * (1 - overlap)))
    positions = list(range(0, length - tile_size + 1, stride))
    last = length - tile_size
    if positions[-1] != last:
        positions.append(last)
    return positions


def inspect_tiled(
    backend,
    image_path: Path,
    reference_paths: list[Path],
    threshold: float,
    heatmap_path: Path,
    artifact_path: Path | None,
    roi: dict | None,
    tile_size: int,
    overlap: float,
    batch_size: int = 8,
) -> AnomalyResult:
    """Inspect a high-resolution image tile-by-tile and merge maps without seams."""
    with Image.open(image_path) as source_image:
        source = source_image.convert("RGB")
        width, height = source.size
        base = np.asarray(source, dtype=np.float32)
    xs = tile_positions(width, tile_size, overlap)
    ys = tile_positions(height, tile_size, overlap)
    if len(xs) * len(ys) > MAX_TILE_COUNT:
        raise ValueError(
            f"切片数量 {len(xs) * len(ys)} 超过安全上限 {MAX_TILE_COUNT}，请增大切片尺寸"
        )
    weighted_sum = np.zeros((height, width), dtype=np.float32)
    weight_sum = np.zeros((height, width), dtype=np.float32)
    scores: list[float] = []
    tiles = []
    for y in ys:
        for x in xs:
            right, bottom = min(width, x + tile_size), min(height, y + tile_size)
            tiles.append((x, y, right, bottom, base[y:bottom, x:right].astype(np.uint8)))
    for offset in range(0, len(tiles), batch_size):
        current = tiles[offset:offset + batch_size]
        predictions = backend.predict_batch(
            [item[4] for item in current], reference_paths, artifact_path
        )
        for (x, y, right, bottom, _), (score, tile_map) in zip(
            current, predictions, strict=True
        ):
            scores.append(float(score))
            if tile_map.shape != (bottom - y, right - x):
                tile_map = np.asarray(
                    Image.fromarray(tile_map.astype(np.float32), mode="F").resize(
                        (right - x, bottom - y), Image.Resampling.BILINEAR
                    )
                )
            tile_height, tile_width = bottom - y, right - x
            vertical = np.hanning(tile_height) if tile_height > 2 else np.ones(tile_height)
            horizontal = np.hanning(tile_width) if tile_width > 2 else np.ones(tile_width)
            weights = np.maximum(np.outer(vertical, horizontal), 0.05).astype(np.float32)
            weighted_sum[y:bottom, x:right] += tile_map * weights
            weight_sum[y:bottom, x:right] += weights
    merged = weighted_sum / np.maximum(weight_sum, 1e-6)
    masked = np.where(_roi_mask(merged.shape, roi), merged, 0)
    score = max(scores, default=0.0)
    decision = "anomaly" if score >= threshold else "normal"
    ReferenceDifferenceBackend._save_heatmap(base, merged, heatmap_path)
    return AnomalyResult(
        score=round(score, 4),
        decision=decision,
        heatmap_path=heatmap_path,
        regions=ReferenceDifferenceBackend._extract_regions(masked) if decision == "anomaly" else [],
        anomaly_map=merged,
    )


def _roi_mask(shape: tuple[int, int], roi: dict | None) -> np.ndarray:
    mask = np.ones(shape, dtype=bool) if not roi else np.zeros(shape, dtype=bool)
    if roi:
        x1, y1, x2, y2 = ReferenceDifferenceBackend._roi_bounds(shape, roi)
        mask[y1:y2, x1:x2] = True
    return mask
