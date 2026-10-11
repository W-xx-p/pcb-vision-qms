from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.config import IMAGE_SIZE
from app.services.artifact_security import verify_artifact_manifest

_CACHE_LOCK = RLock()
_PREDICTION_LOCK = RLock()
_INFERENCER_CACHE: dict[tuple, object] = {}
_TEMPLATE_CACHE: dict[tuple, np.ndarray] = {}


def _artifact_state(artifact_path: Path, names: tuple[str, ...]) -> tuple:
    return tuple(
        (name, (artifact_path / name).stat().st_size, (artifact_path / name).stat().st_mtime_ns)
        for name in names
        if (artifact_path / name).exists()
    )


@dataclass(frozen=True)
class AnomalyResult:
    score: float
    decision: str
    heatmap_path: Path
    regions: list[dict]
    anomaly_map: np.ndarray | None = None


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        normalized = ImageOps.fit(image.convert("RGB"), (IMAGE_SIZE, IMAGE_SIZE))
        return np.asarray(normalized, dtype=np.float32)


class ReferenceDifferenceBackend:
    """Runnable low-data baseline.

    It compares the inspected image with the median appearance of registered normal
    references. The interface is deliberately small so a PatchCore/Anomalib backend
    can replace it without changing the API or quality-management workflow.
    """

    name = "reference-difference"

    def _template(self, reference_paths: list[Path], artifact_path: Path | None) -> np.ndarray:
        if artifact_path and (artifact_path / "normal_template.npy").exists():
            key = (
                "artifact",
                str(artifact_path.resolve()),
                _artifact_state(
                    artifact_path,
                    ("normal_template.npy", "artifact.manifest.json", "artifact.signature"),
                ),
            )
            with _CACHE_LOCK:
                cached = _TEMPLATE_CACHE.get(key)
                if cached is None:
                    verify_artifact_manifest(artifact_path)
                    cached = np.load(artifact_path / "normal_template.npy", allow_pickle=False)
                    _TEMPLATE_CACHE.clear()
                    _TEMPLATE_CACHE[key] = cached
                return cached
        key = (
            "references",
            tuple((str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) for path in reference_paths),
        )
        with _CACHE_LOCK:
            cached = _TEMPLATE_CACHE.get(key)
            if cached is None:
                cached = np.median(np.stack([_load_rgb(path) for path in reference_paths]), axis=0)
                _TEMPLATE_CACHE[key] = cached
            return cached

    def predict_array(
        self,
        image: np.ndarray,
        reference_paths: list[Path],
        artifact_path: Path | None = None,
    ) -> tuple[float, np.ndarray]:
        target = np.asarray(
            ImageOps.fit(Image.fromarray(image.astype(np.uint8)).convert("RGB"), (IMAGE_SIZE, IMAGE_SIZE)),
            dtype=np.float32,
        )
        normal_template = self._template(reference_paths, artifact_path)
        anomaly_map = np.abs(target - normal_template).mean(axis=2) / 255.0
        tail_start = max(1, int(anomaly_map.size * 0.95))
        score = float(np.partition(anomaly_map.ravel(), tail_start)[tail_start:].mean())
        return max(0.0, min(score, 1.0)), anomaly_map

    def predict_batch(
        self,
        images: list[np.ndarray],
        reference_paths: list[Path],
        artifact_path: Path | None = None,
    ) -> list[tuple[float, np.ndarray]]:
        return [self.predict_array(image, reference_paths, artifact_path) for image in images]

    def train(self, reference_paths: list[Path], artifact_dir: Path) -> dict[str, float | int]:
        if not reference_paths:
            raise ValueError("训练至少需要一张正常参考图片")
        references = np.stack([_load_rgb(path) for path in reference_paths])
        template = np.median(references, axis=0).astype(np.float32)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        np.save(artifact_dir / "normal_template.npy", template)
        deviations = np.abs(references - template).mean(axis=(1, 2, 3)) / 255.0
        return {
            "reference_count": len(reference_paths),
            "mean_reference_deviation": round(float(deviations.mean()), 6),
            "max_reference_deviation": round(float(deviations.max()), 6),
        }

    def inspect(
        self,
        image_path: Path,
        reference_paths: list[Path],
        threshold: float,
        heatmap_path: Path,
        artifact_path: Path | None = None,
        roi: dict | None = None,
    ) -> AnomalyResult:
        if not reference_paths and artifact_path is None:
            raise ValueError("该产品尚未登记正常参考图片")

        with Image.open(image_path) as image:
            source = np.asarray(image.convert("RGB"))
        score, channel_delta = self.predict_array(source, reference_paths, artifact_path)
        target = np.asarray(
            ImageOps.fit(Image.fromarray(source).convert("RGB"), (IMAGE_SIZE, IMAGE_SIZE)),
            dtype=np.float32,
        )
        scoring_delta = self._apply_roi(channel_delta, roi)
        # The upper tail captures local defects without allowing one noisy pixel to dominate.
        tail_start = max(1, int(scoring_delta.size * 0.95))
        score = float(np.partition(scoring_delta.ravel(), tail_start)[tail_start:].mean())
        score = max(0.0, min(score, 1.0))

        self._save_heatmap(target, channel_delta, heatmap_path)
        decision = "anomaly" if score >= threshold else "normal"
        region_delta = np.zeros_like(channel_delta)
        if roi:
            x1, y1, x2, y2 = self._roi_bounds(channel_delta.shape, roi)
            region_delta[y1:y2, x1:x2] = channel_delta[y1:y2, x1:x2]
        else:
            region_delta = channel_delta
        regions = self._extract_regions(region_delta) if decision == "anomaly" else []
        return AnomalyResult(
            score=round(score, 4),
            decision=decision,
            heatmap_path=heatmap_path,
            regions=regions,
            anomaly_map=channel_delta,
        )

    @staticmethod
    def _extract_regions(delta: np.ndarray) -> list[dict]:
        """Return a robust normalized bounding box for the strongest anomalous area."""

        cutoff = max(float(np.quantile(delta, 0.985)), 0.08)
        ys, xs = np.where(delta >= cutoff)
        if not len(xs):
            return []
        height, width = delta.shape
        return [{
            "x": round(float(xs.min() / width), 4),
            "y": round(float(ys.min() / height), 4),
            "width": round(float((xs.max() - xs.min() + 1) / width), 4),
            "height": round(float((ys.max() - ys.min() + 1) / height), 4),
            "area_ratio": round(float(len(xs) / delta.size), 4),
            "mean_intensity": round(float(delta[ys, xs].mean()), 4),
        }]

    @staticmethod
    def _roi_bounds(shape: tuple[int, int], roi: dict) -> tuple[int, int, int, int]:
        height, width = shape
        x1 = max(0, min(width - 1, int(float(roi["x"]) * width)))
        y1 = max(0, min(height - 1, int(float(roi["y"]) * height)))
        x2 = max(x1 + 1, min(width, int((float(roi["x"]) + float(roi["width"])) * width)))
        y2 = max(y1 + 1, min(height, int((float(roi["y"]) + float(roi["height"])) * height)))
        return x1, y1, x2, y2

    @classmethod
    def _apply_roi(cls, delta: np.ndarray, roi: dict | None) -> np.ndarray:
        if not roi:
            return delta
        x1, y1, x2, y2 = cls._roi_bounds(delta.shape, roi)
        return delta[y1:y2, x1:x2]

    @staticmethod
    def _save_heatmap(target: np.ndarray, delta: np.ndarray, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        low = float(np.quantile(delta, 0.65))
        high = float(np.quantile(delta, 0.995))
        normalized = np.clip((delta - low) / max(high - low, 1e-6), 0, 1)

        heat = np.zeros_like(target)
        heat[..., 0] = normalized * 255
        heat[..., 1] = np.clip(1 - np.abs(normalized - 0.5) * 2, 0, 1) * 120
        base = Image.fromarray(target.astype(np.uint8))
        overlay = Image.fromarray(heat.astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
        overlay = ImageEnhance.Contrast(overlay).enhance(1.2)
        Image.blend(base, overlay, alpha=0.48).save(destination, quality=92)


class OpenVINOAnomalyBackend:
    def __init__(self, name: str):
        self.name = name

    def _inferencer(self, artifact_path: Path):
        checkpoint = artifact_path / f"{self.name}.xml"
        weights = artifact_path / f"{self.name}.bin"
        checksum_file = artifact_path / f"{self.name}.sha256"
        key = (
            self.name,
            str(artifact_path.resolve()),
            _artifact_state(
                artifact_path,
                (
                    checkpoint.name,
                    weights.name,
                    checksum_file.name,
                    "artifact.manifest.json",
                    "artifact.signature",
                ),
            ),
        )
        with _CACHE_LOCK:
            cached = _INFERENCER_CACHE.get(key)
            if cached is not None:
                return cached
            verify_artifact_manifest(artifact_path)
            if not checkpoint.exists():
                raise ValueError(f"{self.name} OpenVINO检查点不存在")
            if not checksum_file.exists():
                raise ValueError(f"{self.name}完整性清单不存在")
            expected_checksums = json.loads(checksum_file.read_text(encoding="utf-8"))
            for model_file_path in (checkpoint, weights):
                if not model_file_path.exists():
                    raise ValueError(f"模型文件不存在: {model_file_path.name}")
                with model_file_path.open("rb") as model_file:
                    actual_checksum = hashlib.file_digest(model_file, "sha256").hexdigest()
                if not hmac.compare_digest(
                    actual_checksum, str(expected_checksums.get(model_file_path.name, ""))
                ):
                    raise ValueError(f"模型完整性校验失败: {model_file_path.name}")
            try:
                from anomalib.deploy import OpenVINOInferencer
            except ImportError as exc:
                raise RuntimeError(
                    "Anomalib OpenVINO推理后端未安装，请执行 pip install -e '.[anomalib]'"
                ) from exc
            inferencer = OpenVINOInferencer(path=checkpoint, device="AUTO")
            if len(_INFERENCER_CACHE) >= 8:
                _INFERENCER_CACHE.pop(next(iter(_INFERENCER_CACHE)))
            _INFERENCER_CACHE[key] = inferencer
            return inferencer

    def predict_array(
        self,
        image: np.ndarray,
        reference_paths: list[Path],
        artifact_path: Path | None = None,
    ) -> tuple[float, np.ndarray]:
        if artifact_path is None:
            raise ValueError(f"{self.name} 模型工件不存在")
        with _PREDICTION_LOCK:
            prediction = self._inferencer(artifact_path).predict(image=image.astype(np.uint8))
        if isinstance(prediction, (list, tuple)):
            prediction = prediction[0]
        return (
            float(np.asarray(prediction.pred_score).squeeze()),
            np.asarray(prediction.anomaly_map, dtype=np.float32).squeeze(),
        )

    def predict_batch(
        self,
        images: list[np.ndarray],
        reference_paths: list[Path],
        artifact_path: Path | None = None,
    ) -> list[tuple[float, np.ndarray]]:
        inferencer = self._inferencer(artifact_path) if artifact_path else None
        if inferencer is None:
            raise ValueError(f"{self.name} 模型工件不存在")
        results = []
        with _PREDICTION_LOCK:
            for image in images:
                prediction = inferencer.predict(image=image.astype(np.uint8))
                if isinstance(prediction, (list, tuple)):
                    prediction = prediction[0]
                results.append(
                    (
                        float(np.asarray(prediction.pred_score).squeeze()),
                        np.asarray(prediction.anomaly_map, dtype=np.float32).squeeze(),
                    )
                )
        return results

    def inspect(
        self,
        image_path: Path,
        reference_paths: list[Path],
        threshold: float,
        heatmap_path: Path,
        artifact_path: Path | None = None,
        roi: dict | None = None,
    ) -> AnomalyResult:
        if artifact_path is None:
            raise ValueError(f"{self.name} 模型工件不存在")
        with Image.open(image_path) as image:
            source = np.asarray(image.convert("RGB"))
        score, anomaly_map = self.predict_array(source, reference_paths, artifact_path)
        target = _load_rgb(image_path)
        if anomaly_map.shape != target.shape[:2]:
            anomaly_map = np.asarray(
                Image.fromarray(anomaly_map.astype(np.float32), mode="F").resize(
                    target.shape[:2][::-1], Image.Resampling.BILINEAR
                )
            )
        ReferenceDifferenceBackend._save_heatmap(target, anomaly_map, heatmap_path)
        return AnomalyResult(
            # PatchCore emits a model-specific distance, not a probability.
            # Preserve it so the independent release gate can calibrate a real
            # operating threshold instead of collapsing every score above 1.
            score=round(max(0.0, score), 4),
            decision="anomaly" if score >= threshold else "normal",
            heatmap_path=heatmap_path,
            regions=ReferenceDifferenceBackend._extract_regions(
                np.where(_roi_mask(anomaly_map.shape, roi), anomaly_map, 0)
            )
            if score >= threshold else [],
            anomaly_map=anomaly_map,
        )


def _roi_mask(shape: tuple[int, int], roi: dict | None) -> np.ndarray:
    mask = np.ones(shape, dtype=bool) if not roi else np.zeros(shape, dtype=bool)
    if roi:
        x1, y1, x2, y2 = ReferenceDifferenceBackend._roi_bounds(shape, roi)
        mask[y1:y2, x1:x2] = True
    return mask


def get_backend(name: str = "reference") -> ReferenceDifferenceBackend | OpenVINOAnomalyBackend:
    if name in {"patchcore", "padim", "efficientad"}:
        return OpenVINOAnomalyBackend(name)
    return ReferenceDifferenceBackend()
