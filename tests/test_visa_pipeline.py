from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.benchmark_visa import benchmark_category


def _sample(path: Path, defect: bool = False) -> None:
    image = Image.new("RGB", (256, 256), (35, 70, 45))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 235, 235), outline=(180, 160, 90), width=4)
    if defect:
        draw.rectangle((170, 175, 220, 225), fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def test_reference_visa_benchmark_writes_reproducible_metrics(tmp_path):
    dataset = tmp_path / "visa"
    category = dataset / "1cls" / "pcb1"
    _sample(category / "train" / "good" / "train-1.png")
    _sample(category / "train" / "good" / "train-2.png")
    _sample(category / "test" / "good" / "good-1.png")
    _sample(category / "test" / "good" / "good-2.png")
    for name in ("bad-1.png", "bad-2.png"):
        _sample(category / "test" / "bad" / name, defect=True)
        mask = Image.new("L", (256, 256), 0)
        ImageDraw.Draw(mask).rectangle((170, 175, 220, 225), fill=255)
        mask_path = category / "ground_truth" / "bad" / name
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask.save(mask_path)
    (dataset / "manifest.json").write_text(
        json.dumps({"manifest_sha256": "0" * 64}), encoding="utf-8"
    )
    result = benchmark_category(dataset, tmp_path / "output", "pcb1", "reference", 256)
    assert result["image_auroc"] == 1.0
    assert result["pixel_auroc"] is not None
    assert result["classification"]["f1"] == 1.0
