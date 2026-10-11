from __future__ import annotations

import argparse
import csv
import ctypes
import gc
import json
import shutil
import statistics
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.anomaly import ReferenceDifferenceBackend, get_backend
from app.services.artifact_security import create_artifact_manifest
from app.services.benchmarking import binary_auroc, latency_percentiles, pixel_auroc
from app.services.evaluation import evaluate_scores, find_best_threshold
from app.services.training import _train_anomalib

SUPPORTED_BACKENDS = ("reference", "patchcore", "padim", "efficientad")
EFFICIENTAD_TRAIN_BATCHES = 8


def _release_process_memory() -> None:
    """Return Python and native ML allocations between benchmark runs."""

    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (AttributeError, OSError):
            pass


def _images(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})


def _mask_for(image: Path, category_root: Path, shape: tuple[int, int]) -> np.ndarray:
    mask_dir = category_root / "ground_truth" / "bad"
    candidates = [mask_dir / image.name, mask_dir / f"{image.stem}.png"]
    mask_path = next((item for item in candidates if item.exists()), None)
    if not mask_path:
        stem_matches = list(mask_dir.glob(f"{image.stem}*"))
        mask_path = stem_matches[0] if stem_matches else None
    if not mask_path:
        raise FileNotFoundError(f"异常样本缺少像素掩码: {image.name}")
    with Image.open(mask_path) as mask:
        resized = mask.convert("L").resize(shape[::-1], Image.Resampling.NEAREST)
        return (np.asarray(resized) > 0).astype(np.uint8)


def _metric_map(anomaly_map: np.ndarray, image_size: int) -> np.ndarray:
    """Normalize every backend to one pixel-metric resolution.

    Model-native maps legitimately differ in shape.  Comparing them without a
    common raster makes the pixel metric unfair and can multiply AUROC memory.
    """

    values = np.asarray(anomaly_map, dtype=np.float32).squeeze()
    if values.ndim != 2:
        raise ValueError(f"异常图必须是二维数组，实际形状: {values.shape}")
    if values.shape == (image_size, image_size):
        return values
    return np.asarray(
        Image.fromarray(values, mode="F").resize(
            (image_size, image_size), Image.Resampling.BILINEAR
        ),
        dtype=np.float32,
    )


def benchmark_category(
    dataset_root: Path,
    output: Path,
    category: str,
    backend_name: str,
    image_size: int,
    seed: int = 42,
    train_samples: int | None = 64,
    patchcore_coreset_ratio: float = 0.01,
) -> dict:
    category_root = dataset_root / "1cls" / category
    train_paths = _images(category_root / "train" / "good")
    good_paths = _images(category_root / "test" / "good")
    bad_paths = _images(category_root / "test" / "bad")
    if not train_paths or not good_paths or not bad_paths:
        raise ValueError(f"{category} 数据不完整，需要 train/good、test/good 和 test/bad")
    if train_samples is not None and train_samples < len(train_paths):
        training_generator = np.random.default_rng(seed)
        selected = training_generator.permutation(len(train_paths))[:train_samples]
        train_paths = sorted(train_paths[int(index)] for index in selected)
    artifact = output / "artifacts" / category / backend_name / f"seed-{seed}"
    if artifact.exists():
        shutil.rmtree(artifact)
    if backend_name == "reference":
        ReferenceDifferenceBackend().train(train_paths, artifact)
    else:
        parameters = {"image_size": image_size, "seed": seed}
        if backend_name == "patchcore":
            parameters["coreset_sampling_ratio"] = patchcore_coreset_ratio
        elif backend_name == "efficientad":
            # Bound memory for reproducible CPU runs on 4 GiB CI/dev hosts.
            parameters["limit_train_batches"] = EFFICIENTAD_TRAIN_BATCHES
        _train_anomalib(
            train_paths, artifact, backend_name, parameters
        )
    create_artifact_manifest(artifact)
    backend = get_backend(backend_name)
    samples = [(path, "normal") for path in good_paths] + [
        (path, "anomaly") for path in bad_paths
    ]
    generator = np.random.default_rng(seed)
    calibration_paths: set[Path] = set()
    for paths in (good_paths, bad_paths):
        count = max(1, round(len(paths) * 0.2))
        indices = generator.permutation(len(paths))[:count]
        calibration_paths.update(paths[int(index)] for index in indices)
    calibration = [sample for sample in samples if sample[0] in calibration_paths]
    evaluation = [sample for sample in samples if sample[0] not in calibration_paths]

    scores: dict[Path, float] = {}
    maps: dict[Path, np.ndarray] = {}
    latencies: list[float] = []
    for path, _ in samples:
        with Image.open(path) as image:
            pixels = np.asarray(image.convert("RGB"))
        started = perf_counter()
        score, anomaly_map = backend.predict_array(pixels, train_paths, artifact)
        latencies.append((perf_counter() - started) * 1000)
        scores[path] = float(score)
        maps[path] = _metric_map(anomaly_map, image_size)
    calibrated = find_best_threshold([(scores[path], label) for path, label in calibration])
    scored_evaluation = [(scores[path], label) for path, label in evaluation]
    classification = evaluate_scores(scored_evaluation, calibrated.threshold).as_dict()
    labels = [1 if label == "anomaly" else 0 for _, label in evaluation]
    image_auc = binary_auroc(labels, [score for score, _ in scored_evaluation])
    pixel_maps: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for path, label in evaluation:
        if path not in maps:
            continue
        anomaly_map = maps[path]
        pixel_maps.append(anomaly_map)
        masks.append(
            np.zeros(anomaly_map.shape, dtype=np.uint8)
            if label == "normal"
            else _mask_for(path, category_root, anomaly_map.shape)
        )
    return {
        "category": category,
        "backend": backend_name,
        "seed": seed,
        "train_count": len(train_paths),
        "calibration_count": len(calibration),
        "evaluation_count": len(evaluation),
        "image_auroc": image_auc,
        "pixel_auroc": pixel_auroc(pixel_maps, masks),
        "classification": classification,
        "latency": latency_percentiles(latencies),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="在 VisA PCB1-PCB4 上运行统一多模型基准")
    parser.add_argument("--dataset", type=Path, default=Path("data/public/visa"))
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/visa"))
    parser.add_argument("--categories", nargs="+", default=["pcb1", "pcb2", "pcb3", "pcb4"])
    parser.add_argument("--backends", nargs="+", choices=SUPPORTED_BACKENDS, default=SUPPORTED_BACKENDS)
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument(
        "--train-samples",
        type=int,
        default=64,
        help="每类别用于训练的正常样本数；0 表示使用全部训练集（默认 64-shot）",
    )
    parser.add_argument("--patchcore-coreset-ratio", type=float, default=0.01)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    args = parser.parse_args()
    manifest_path = args.dataset / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("缺少 manifest.json，请先运行 scripts/prepare_visa.py")
    dataset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output / "checkpoint.json"
    checkpoint = {
        "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
        "image_size": args.image_size,
        "train_samples": args.train_samples,
        "patchcore_coreset_ratio": args.patchcore_coreset_ratio,
        "efficientad_train_batches": EFFICIENTAD_TRAIN_BATCHES,
        "results": [],
    }
    if checkpoint_path.exists():
        existing = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            existing.get("dataset_manifest_sha256") == dataset_manifest["manifest_sha256"]
            and existing.get("image_size") == args.image_size
            and existing.get("train_samples") == args.train_samples
            and existing.get("patchcore_coreset_ratio") == args.patchcore_coreset_ratio
            and existing.get("efficientad_train_batches") == EFFICIENTAD_TRAIN_BATCHES
        ):
            checkpoint = existing
    results = list(checkpoint["results"])
    completed = {
        (item["category"], item["backend"], int(item["seed"])) for item in results
    }
    requested = [
        (category, backend, seed)
        for category in args.categories
        for backend in args.backends
        for seed in args.seeds
    ]
    for index, (category, backend, seed) in enumerate(requested, start=1):
        if (category, backend, seed) in completed:
            print(f"[{index}/{len(requested)}] skip {category}/{backend}/seed-{seed}", flush=True)
            continue
        print(f"[{index}/{len(requested)}] start {category}/{backend}/seed-{seed}", flush=True)
        result = benchmark_category(
            args.dataset,
            args.output,
            category,
            backend,
            args.image_size,
            seed,
            None if args.train_samples == 0 else args.train_samples,
            args.patchcore_coreset_ratio,
        )
        results.append(result)
        checkpoint["results"] = results
        temporary = checkpoint_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(checkpoint_path)
        print(
            f"[{index}/{len(requested)}] done {category}/{backend}/seed-{seed} "
            f"image_auc={result['image_auroc']} pixel_auc={result['pixel_auroc']}",
            flush=True,
        )
        _release_process_memory()
    aggregates = []
    for category in args.categories:
        for backend in args.backends:
            group = [
                item for item in results
                if item["category"] == category and item["backend"] == backend
            ]
            aggregate = {"category": category, "backend": backend, "repeats": len(group)}
            for key, values in {
                "image_auroc": [item["image_auroc"] for item in group],
                "pixel_auroc": [item["pixel_auroc"] for item in group],
                "f1": [item["classification"]["f1"] for item in group],
                "recall": [item["classification"]["recall"] for item in group],
                "p95_ms": [item["latency"]["p95_ms"] for item in group],
            }.items():
                valid = [float(value) for value in values if value is not None]
                aggregate[f"{key}_mean"] = round(statistics.fmean(valid), 6) if valid else None
                aggregate[f"{key}_std"] = (
                    round(statistics.pstdev(valid), 6) if len(valid) > 1 else 0.0
                )
            aggregates.append(aggregate)
    report = {
        "schema_version": 1,
        "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
        "protocol": {
            "train_samples_per_category": "all" if args.train_samples == 0 else args.train_samples,
            "evaluation_split": "complete official test split",
            "pixel_metric_resolution": [args.image_size, args.image_size],
            "seeds": args.seeds,
            "patchcore_coreset_ratio": args.patchcore_coreset_ratio,
            "efficientad_auxiliary_data": "local normal training set",
            "efficientad_train_batches": EFFICIENTAD_TRAIN_BATCHES,
        },
        "results": results,
        "aggregates": aggregates,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (args.output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=[
                "category", "backend", "seed", "train_count", "evaluation_count", "image_auroc",
                "pixel_auroc", "f1", "recall", "p95_ms",
            ],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    **{key: item[key] for key in ("category", "backend", "seed", "train_count", "evaluation_count", "image_auroc", "pixel_auroc")},
                    "f1": item["classification"]["f1"],
                    "recall": item["classification"]["recall"],
                    "p95_ms": item["latency"]["p95_ms"],
                }
            )
    with (args.output / "aggregate.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as target:
        writer = csv.DictWriter(target, fieldnames=list(aggregates[0]))
        writer.writeheader()
        writer.writerows(aggregates)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
