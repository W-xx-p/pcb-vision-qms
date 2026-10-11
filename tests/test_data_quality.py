from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.data_quality import validate_dataset_samples


def _image(path: Path, offset: int = 0) -> None:
    image = Image.new("RGB", (128, 128), (20, 60, 40))
    ImageDraw.Draw(image).rectangle((20 + offset, 20, 80 + offset, 80), fill=(180, 160, 70))
    image.save(path)


def test_data_quality_blocks_exact_cross_split_and_batch_leakage(tmp_path):
    train = tmp_path / "train.png"
    test = tmp_path / "test.png"
    _image(train)
    test.write_bytes(train.read_bytes())
    report = validate_dataset_samples(
        [
            {"path": train, "filename": "train.png", "split": "train", "metadata": {"batch_id": "LOT-1"}},
            {"path": test, "filename": "test.png", "split": "test", "metadata": {"batch_id": "LOT-1"}},
        ]
    )
    assert report["passed"] is False
    assert {item["type"] for item in report["errors"]} == {
        "exact_duplicate",
        "batch_cross_split",
    }


def test_data_quality_accepts_distinct_split_samples(tmp_path):
    train = tmp_path / "train.png"
    test = tmp_path / "test.png"
    _image(train)
    _image(test, offset=25)
    report = validate_dataset_samples(
        [
            {"path": train, "filename": "train.png", "split": "train", "metadata": {}},
            {"path": test, "filename": "test.png", "split": "test", "metadata": {}},
        ]
    )
    assert report["passed"] is True
