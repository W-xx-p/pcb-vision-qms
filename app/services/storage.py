from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import MAX_IMAGE_PIXELS, MEDIA_DIR

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned or "item"


async def save_image(upload: UploadFile, folder: Path) -> tuple[Path, str]:
    suffix = Path(upload.filename or "upload.png").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, "仅支持 JPG、PNG、BMP 和 WEBP 图片")

    folder.mkdir(parents=True, exist_ok=True)
    original_name = Path(upload.filename or f"upload{suffix}").name
    destination = folder / f"{uuid4().hex}{suffix}"
    content = await upload.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "单张图片不能超过 20 MB")
    destination.write_bytes(content)

    too_large = False
    try:
        with Image.open(destination) as image:
            too_large = image.width * image.height > MAX_IMAGE_PIXELS
            image.verify()
    except (UnidentifiedImageError, OSError):
        destination.unlink(missing_ok=True)
        raise HTTPException(415, "文件不是有效图片") from None
    if too_large:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            413,
            f"图片像素数超过安全上限 {MAX_IMAGE_PIXELS}，请降低分辨率或调整生产配置",
        )
    return destination, original_name


def media_url(path: str | Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        relative = candidate.resolve().relative_to(MEDIA_DIR.resolve())
        return "/media/" + relative.as_posix()
    return "/media/" + str(path).replace("\\", "/").lstrip("/")

