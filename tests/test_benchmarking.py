from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.services.anomaly import AnomalyResult
from app.services.benchmarking import binary_auroc, latency_percentiles, pixel_auroc
from app.services.tiling import inspect_tiled, tile_positions


class BrightPixelBackend:
    def __init__(self):
        self.batch_calls = 0

    def predict_batch(self, images, reference_paths, artifact_path):
        self.batch_calls += 1
        return [
            (float(values.max()), values)
            for image in images
            for values in [image.astype(np.float32).mean(axis=2) / 255]
        ]

    def inspect(self, image_path: Path, **kwargs) -> AnomalyResult:
        with Image.open(image_path) as image:
            values = np.asarray(image.convert("RGB"), dtype=np.float32).mean(axis=2) / 255
        score = float(values.max())
        heatmap = kwargs["heatmap_path"]
        Image.fromarray((values * 255).astype(np.uint8)).save(heatmap)
        return AnomalyResult(score, "anomaly", heatmap, [], values)


def test_binary_and_pixel_auroc_with_ties():
    assert binary_auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert binary_auroc([0, 1], [0.5, 0.5]) == 0.5
    assert pixel_auroc(
        [np.array([[0.0, 0.1], [0.8, 0.9]])],
        [np.array([[0, 0], [1, 1]])],
    ) == 1.0
    assert latency_percentiles([1, 2, 3])["p95_ms"] > 2


def test_high_resolution_tiles_cover_edges_and_merge_coordinates(tmp_path):
    image_path = tmp_path / "large.png"
    image = np.zeros((610, 930, 3), dtype=np.uint8)
    image[580:605, 900:925] = 255
    Image.fromarray(image).save(image_path)
    assert tile_positions(930, 256, 0.25)[-1] == 674
    backend = BrightPixelBackend()
    result = inspect_tiled(
        backend=backend,
        image_path=image_path,
        reference_paths=[],
        threshold=0.5,
        heatmap_path=tmp_path / "heat.jpg",
        artifact_path=None,
        roi=None,
        tile_size=256,
        overlap=0.25,
    )
    assert result.decision == "anomaly"
    assert result.anomaly_map is not None
    assert result.anomaly_map.shape == (610, 930)
    assert result.regions[0]["x"] > 0.9
    expected_tiles = len(tile_positions(930, 256, 0.25)) * len(
        tile_positions(610, 256, 0.25)
    )
    assert backend.batch_calls == int(np.ceil(expected_tiles / 8))
