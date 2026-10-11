from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.data_quality import perceptual_dhash, validate_dataset_samples

VISA_ARCHIVE_URL = (
    "https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar"
)
VISA_SPLIT_URL = (
    "https://raw.githubusercontent.com/amazon-science/spot-diff/main/split_csv/1cls.csv"
)
PCB_CATEGORIES = ("pcb1", "pcb2", "pcb3", "pcb4")


def download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    head = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "pcb-qms-benchmark/1.0"}
    )
    with urllib.request.urlopen(head, timeout=60) as response:
        total = int(response.headers["Content-Length"])
    prefix_size = partial.stat().st_size if partial.exists() else 0
    if prefix_size > total:
        partial.unlink()
        prefix_size = 0
    remaining = total - prefix_size
    if remaining:
        worker_count = min(8, max(1, remaining // (32 * 1024 * 1024)))
        chunk_size = (remaining + worker_count - 1) // worker_count
        ranges = [
            (index, prefix_size + index * chunk_size, min(total - 1, prefix_size + (index + 1) * chunk_size - 1))
            for index in range(worker_count)
            if prefix_size + index * chunk_size < total
        ]

        def fetch_range(item: tuple[int, int, int]) -> Path:
            index, start, end = item
            chunk = partial.with_suffix(partial.suffix + f".{index}")
            expected = end - start + 1
            if chunk.exists() and chunk.stat().st_size == expected:
                return chunk
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "pcb-qms-benchmark/1.0",
                    "Range": f"bytes={start}-{end}",
                },
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                if response.status != 206:
                    raise RuntimeError("数据源不支持断点范围下载")
                with chunk.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if chunk.stat().st_size != expected:
                raise RuntimeError(f"下载分片 {index} 长度校验失败")
            return chunk

        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            chunks = list(pool.map(fetch_range, ranges))
        with partial.open("ab") as output:
            for chunk in chunks:
                with chunk.open("rb") as source:
                    shutil.copyfileobj(source, output)
                chunk.unlink()
    if partial.stat().st_size != total:
        raise RuntimeError("下载文件总长度校验失败")
    partial.replace(destination)
    return destination


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"VisA 压缩包包含不允许的链接: {member.name}")
            target = (destination / member.name).resolve()
            if target != resolved_destination and resolved_destination not in target.parents:
                raise ValueError(f"VisA 压缩包路径越界: {member.name}")
        bundle.extractall(destination, filter="data")


def _field(row: dict[str, str], *names: str) -> str:
    normalized = {key.strip().lower(): value for key, value in row.items() if key}
    for name in names:
        if normalized.get(name):
            return normalized[name].strip()
    return ""


def _find_source(root: Path, relative: str) -> Path:
    candidate = root / relative
    if candidate.exists():
        return candidate
    matches = list(root.rglob(Path(relative).name))
    suffix = Path(relative).as_posix().lower()
    for match in matches:
        if match.as_posix().lower().endswith(suffix):
            return match
    raise FileNotFoundError(f"VisA 文件不存在: {relative}")


def prepare(
    raw_root: Path,
    split_csv: Path,
    output: Path,
    categories: set[str],
    strict_quality_gate: bool = False,
) -> dict:
    records: list[dict] = []
    with split_csv.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            category = _field(row, "object", "category", "object_name").lower()
            if category not in categories:
                continue
            split = _field(row, "split", "set").lower()
            split = "train" if split.startswith("train") else "test"
            label_text = _field(row, "label", "class").lower()
            label = "good" if label_text in {"normal", "good", "0"} else "bad"
            image_relative = _field(row, "image", "image_path", "img_path")
            mask_relative = _field(row, "mask", "mask_path", "ground_truth")
            if not image_relative:
                raise ValueError("VisA split CSV 缺少 image 列")
            source_image = _find_source(raw_root, image_relative)
            image_destination = output / "1cls" / category / split / label / source_image.name
            image_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_image, image_destination)
            with Image.open(image_destination) as image:
                width, height = image.size
            mask_path = None
            if split == "test" and label == "bad" and mask_relative:
                source_mask = _find_source(raw_root, mask_relative)
                mask_path = output / "1cls" / category / "ground_truth" / "bad" / source_mask.name
                mask_path.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source_mask) as mask:
                    mask.convert("L").point(lambda value: 255 if value else 0).save(mask_path)
            digest = hashlib.sha256(image_destination.read_bytes()).hexdigest()
            records.append(
                {
                    "category": category,
                    "split": split,
                    "label": label,
                    "image": image_destination.relative_to(output).as_posix(),
                    "mask": mask_path.relative_to(output).as_posix() if mask_path else None,
                    "sha256": digest,
                    "width": width,
                    "height": height,
                    "perceptual_hash": perceptual_dhash(image_destination),
                }
            )
    if not records:
        raise ValueError("没有找到所选 PCB 类别，请确认 VisA 解压目录和 1cls.csv")
    quality_samples = [
        {
            "path": output / item["image"],
            "filename": item["image"],
            "sha256": item["sha256"],
            "perceptual_hash": item["perceptual_hash"],
            "split": item["split"],
            "metadata": {},
        }
        for item in records
    ]
    quality_gate = validate_dataset_samples(quality_samples)
    if strict_quality_gate and not quality_gate["passed"]:
        raise ValueError(
            f"VisA 数据未通过防泄漏门禁，共 {len(quality_gate['errors'])} 个跨集合冲突"
        )
    manifest = {
        "schema_version": 1,
        "source": "Amazon VisA 20220922",
        "source_url": VISA_ARCHIVE_URL,
        "split_url": VISA_SPLIT_URL,
        "license_notice": "Use is subject to the VisA dataset terms distributed by its authors.",
        "categories": sorted(categories),
        "sample_count": len(records),
        "quality_gate": quality_gate,
        "samples": sorted(records, key=lambda item: item["image"]),
    }
    canonical = json.dumps(manifest["samples"], sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="下载并准备 VisA PCB1-PCB4 公开评测集")
    parser.add_argument("--work-dir", type=Path, default=Path("data/public/visa-work"))
    parser.add_argument("--output", type=Path, default=Path("data/public/visa"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--split-csv", type=Path)
    parser.add_argument("--categories", nargs="+", choices=PCB_CATEGORIES, default=PCB_CATEGORIES)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--strict-quality-gate", action="store_true")
    args = parser.parse_args()

    archive = args.archive or args.work_dir / "VisA_20220922.tar"
    split_csv = args.split_csv or args.work_dir / "1cls.csv"
    if not archive.exists():
        if args.no_download:
            raise FileNotFoundError(archive)
        download(VISA_ARCHIVE_URL, archive)
    if not split_csv.exists():
        if args.no_download:
            raise FileNotFoundError(split_csv)
        download(VISA_SPLIT_URL, split_csv)
    extracted = args.work_dir / "extracted"
    if not extracted.exists() or not any(extracted.iterdir()):
        safe_extract(archive, extracted)
    result = prepare(
        extracted,
        split_csv,
        args.output,
        set(args.categories),
        strict_quality_gate=args.strict_quality_gate,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
