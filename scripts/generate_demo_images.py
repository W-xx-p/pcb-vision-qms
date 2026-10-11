from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


def draw_board(defect: bool = False, variation: int = 0) -> Image.Image:
    image = Image.new("RGB", (720, 480), (20 + variation, 82 + variation, 57 + variation))
    draw = ImageDraw.Draw(image)
    for row in range(4):
        for column in range(7):
            x = 70 + column * 88
            y = 60 + row * 98
            draw.rectangle((x, y, x + 48, y + 28), fill=(34, 38, 42), outline=(165, 174, 165), width=2)
            draw.line((x + 24, y + 28, x + 24, y + 48), fill=(210, 174, 72), width=3)
    draw.rectangle((30, 24, 690, 450), outline=(207, 180, 77), width=4)
    draw.text((42, 432), "PCB-A100  DEMO", fill=(235, 235, 215))
    if defect:
        draw.rectangle((425, 245, 492, 310), fill=(215, 38, 42), outline=(255, 220, 220), width=4)
        draw.line((360, 160, 540, 330), fill=(240, 220, 200), width=7)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic PCB images for local demo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("demo-data")
    try:
        normal_dir = output / "normal"
        test_dir = output / "test"
        normal_dir.mkdir(parents=True, exist_ok=True)
        test_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        if args.output is not None:
            raise
        output = Path(tempfile.gettempdir()) / "pcb-qms-demo"
        normal_dir = output / "normal"
        test_dir = output / "test"
        normal_dir.mkdir(parents=True, exist_ok=True)
        test_dir.mkdir(parents=True, exist_ok=True)
    for index, variation in enumerate((-3, -1, 0, 1, 3), start=1):
        draw_board(variation=variation).save(normal_dir / f"normal-{index}.png")
    draw_board().save(test_dir / "normal-test.png")
    draw_board(defect=True).save(test_dir / "anomaly-test.png")
    print(f"Demo images generated at {output.resolve()}")


if __name__ == "__main__":
    main()
