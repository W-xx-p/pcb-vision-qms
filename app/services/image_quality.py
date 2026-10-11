from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class ImageQuality:
    brightness: float
    contrast: float
    sharpness: float
    accepted: bool
    warnings: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def analyze_image_quality(path: Path) -> ImageQuality:
    with Image.open(path) as image:
        gray = np.asarray(
            ImageOps.fit(image.convert("L"), (256, 256)),
            dtype=np.float32,
        ) / 255.0

    brightness = float(gray.mean())
    contrast = float(gray.std())
    gradient_x = np.abs(np.diff(gray, axis=1)).mean()
    gradient_y = np.abs(np.diff(gray, axis=0)).mean()
    sharpness = float((gradient_x + gradient_y) / 2)

    warnings: list[str] = []
    if brightness < 0.08:
        warnings.append("图像过暗")
    elif brightness > 0.95:
        warnings.append("图像过亮")
    if contrast < 0.025:
        warnings.append("图像对比度过低")
    if sharpness < 0.008:
        warnings.append("图像可能模糊")

    return ImageQuality(
        brightness=round(brightness, 4),
        contrast=round(contrast, 4),
        sharpness=round(sharpness, 4),
        accepted=not warnings,
        warnings=warnings,
    )

