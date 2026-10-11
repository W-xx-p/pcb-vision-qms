from __future__ import annotations

import hashlib
import json
import os
import shutil
import traceback
from pathlib import Path

from app.config import MODEL_DIR
from app.database import connect, fetch_all, fetch_one, utc_now
from app.services.anomaly import ReferenceDifferenceBackend
from app.services.artifact_security import create_artifact_manifest
from app.services.infrastructure import materialize_file, mirror_file
from app.services.lineage import dataset_version_or_none, ensure_dataset_quality
from app.services.model_governance import evaluate_candidate
from app.services.storage import safe_segment


def create_training_job(
    product_id: int,
    backend: str,
    parameters: dict,
    actor: str = "system",
) -> tuple[int, int]:
    dataset_version_id = parameters.get("dataset_version_id")
    if dataset_version_id:
        dataset_version = dataset_version_or_none(dataset_version_id, product_id)
        if not dataset_version:
            raise ValueError("数据集版本不存在、未冻结或不属于当前产品")
        dataset_stats = ensure_dataset_quality(
            int(dataset_version_id), json.loads(dataset_version.get("stats_json") or "{}")
        )
        if not dataset_stats.get("quality_gate", {}).get("passed", False):
            raise ValueError("数据集未通过重复样本、近重复样本和批次泄漏门禁")
    with connect() as db:
        current = db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM model_versions WHERE product_id = ?",
            (product_id,),
        ).fetchone()[0]
        cursor = db.execute(
            """
            INSERT INTO model_versions(
                product_id, version, backend, status, parameters_json,
                dataset_version_id, created_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?) RETURNING id
            """,
            (
                product_id,
                int(current) + 1,
                backend,
                json.dumps(parameters),
                dataset_version_id,
                utc_now(),
            ),
        )
        model_id = int(cursor.scalar_one())
        cursor = db.execute(
            """
            INSERT INTO training_jobs(model_id, status, progress, message, created_at)
            VALUES (?, 'queued', 0, '等待训练', ?) RETURNING id
            """,
            (model_id, utc_now()),
        )
        job_id = int(cursor.scalar_one())
        db.execute(
            """INSERT INTO experiment_runs(
                dataset_version_id, model_id, backend, status, parameters_json,
                code_version, image_digest, created_by, created_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (
                dataset_version_id,
                model_id,
                backend,
                json.dumps(parameters),
                os.getenv("PCB_QMS_CODE_VERSION", "unknown")[:120],
                os.getenv("PCB_QMS_IMAGE_DIGEST", "unknown")[:200],
                actor,
                utc_now(),
            ),
        )
    return model_id, job_id


def run_training_job(job_id: int) -> None:
    job = fetch_one(
        """
        SELECT j.*, m.product_id, m.backend, m.version, m.parameters_json,
               m.dataset_version_id,
               p.code AS product_code
        FROM training_jobs j
        JOIN model_versions m ON m.id = j.model_id
        JOIN products p ON p.id = m.product_id
        WHERE j.id = ?
        """,
        (job_id,),
    )
    if not job:
        return

    _update_job(job_id, job["model_id"], "running", 10, "正在校验训练数据", started=True)
    try:
        if job.get("dataset_version_id"):
            reference_rows = fetch_all(
                """SELECT object_path AS path FROM dataset_samples
                   WHERE dataset_version_id = ? AND split = 'train' AND label = 'normal'
                   ORDER BY id""",
                (job["dataset_version_id"],),
            )
        else:
            reference_rows = fetch_all(
                "SELECT path FROM reference_images WHERE product_id = ? ORDER BY id",
                (job["product_id"],),
            )
        references = [materialize_file(row["path"]) for row in reference_rows]
        if not references:
            raise ValueError("没有正常参考图片，无法训练")

        artifact_dir = (
            MODEL_DIR
            / safe_segment(job["product_code"])
            / f"v{job['version']}-{safe_segment(job['backend'])}"
        )
        if artifact_dir.exists():
            resolved_artifact = artifact_dir.resolve()
            if MODEL_DIR.resolve() not in resolved_artifact.parents:
                raise RuntimeError("模型产物路径超出允许目录")
            shutil.rmtree(artifact_dir)

        _update_job(job_id, job["model_id"], "running", 35, "正在构建模型")
        if job["backend"] == "reference":
            metrics = ReferenceDifferenceBackend().train(references, artifact_dir)
        elif job["backend"] in {"patchcore", "padim", "efficientad"}:
            metrics = _train_anomalib(
                references,
                artifact_dir,
                job["backend"],
                json.loads(job["parameters_json"]),
            )
        else:
            raise ValueError(f"不支持的训练后端：{job['backend']}")

        _update_job(job_id, job["model_id"], "running", 80, "正在执行独立验证与发布门禁")
        gate_report = evaluate_candidate(job["product_id"], job["backend"], artifact_dir)
        metrics = {**metrics, "release_gate": gate_report}
        gate_status = "passed" if gate_report["passed"] else "blocked"
        _, artifact_signature = create_artifact_manifest(artifact_dir)
        _update_job(job_id, job["model_id"], "running", 90, "正在登记候选模型")
        uploaded = []
        for artifact in artifact_dir.rglob("*"):
            if artifact.is_file():
                key = mirror_file(artifact)
                if key:
                    uploaded.append(key)
        stored_artifact = (
            artifact_dir.relative_to(MODEL_DIR.parent).as_posix() if uploaded else str(artifact_dir)
        )
        with connect() as db:
            db.execute(
                """
                UPDATE model_versions
                SET status = 'ready', artifact_path = ?, metrics_json = ?,
                    gate_status = ?, gate_report_json = ?, is_active = FALSE,
                    release_stage = ?, artifact_signature = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    stored_artifact,
                    json.dumps(metrics),
                    gate_status,
                    json.dumps(gate_report),
                    "approved" if gate_report["passed"] else "rejected",
                    artifact_signature,
                    utc_now(),
                    job["model_id"],
                ),
            )
            db.execute(
                """
                UPDATE training_jobs
                SET status = 'completed', progress = 100, message = '训练完成', finished_at = ?
                WHERE id = ?
                """,
                (utc_now(), job_id),
            )
            db.execute(
                """UPDATE experiment_runs SET status = 'completed', metrics_json = ?,
                   finished_at = ? WHERE model_id = ?""",
                (json.dumps(metrics), utc_now(), job["model_id"]),
            )
        if uploaded:
            shutil.rmtree(artifact_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - persist every training failure for retry/DLQ
        message = f"{type(exc).__name__}: {exc}"
        with connect() as db:
            db.execute(
                "UPDATE model_versions SET status = 'failed', completed_at = ? WHERE id = ?",
                (utc_now(), job["model_id"]),
            )
            db.execute(
                """
                UPDATE training_jobs
                SET status = 'failed', message = ?, finished_at = ?
                WHERE id = ?
                """,
                (message[:500], utc_now(), job_id),
            )
            db.execute(
                """UPDATE experiment_runs SET status = 'failed', metrics_json = ?,
                   finished_at = ? WHERE model_id = ?""",
                (json.dumps({"error": message[:500]}), utc_now(), job["model_id"]),
            )
        traceback.print_exc()


def _train_anomalib(
    reference_paths: list[Path],
    artifact_dir: Path,
    backend: str,
    parameters: dict,
) -> dict:
    """Train and export an Anomalib model through one reproducible adapter."""

    try:
        from anomalib.data import Folder
        from anomalib.engine import Engine
        from anomalib.models import EfficientAd, Padim, Patchcore
        from lightning import seed_everything
    except ImportError as exc:
        raise RuntimeError(
            "Anomalib 后端未安装，请执行 pip install -e '.[anomalib]'"
        ) from exc

    seed = int(parameters.get("seed", 42))
    seed_everything(seed, workers=True)
    normal_dir = artifact_dir / "dataset" / "normal"
    normal_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(reference_paths):
        shutil.copy2(source, normal_dir / f"{index:05d}{source.suffix.lower()}")

    datamodule = Folder(
        name=artifact_dir.name,
        root=artifact_dir / "dataset",
        normal_dir="normal",
        # PatchCore training only needs normal samples. Disabling Anomalib's
        # implicit holdout split keeps true one/few-shot datasets valid; the
        # platform performs an independent human-reviewed release gate below.
        test_split_mode="none",
        val_split_mode="none",
        train_batch_size=1,
        eval_batch_size=1,
        num_workers=0,
    )
    image_size = int(parameters.get("image_size", 256))
    model_classes = {"patchcore": Patchcore, "padim": Padim, "efficientad": EfficientAd}
    model_class = model_classes[backend]
    model_kwargs = {"pre_processor": model_class.configure_pre_processor((image_size, image_size))}
    if backend == "patchcore":
        model_kwargs["coreset_sampling_ratio"] = float(
            parameters.get("coreset_sampling_ratio", 0.1)
        )
    if backend == "efficientad":
        # EfficientAD otherwise downloads the 1.56 GB Imagenette dataset at
        # training time.  Reuse the frozen normal set as the auxiliary
        # hard-feature source so edge/offline deployments remain reproducible.
        model_kwargs["imagenet_dir"] = artifact_dir / "dataset"
    model = model_class(**model_kwargs)
    engine = Engine(
        default_root_dir=artifact_dir / "runs",
        max_epochs=1,
        limit_train_batches=parameters.get("limit_train_batches", 1.0),
        limit_val_batches=0,
        num_sanity_val_steps=0,
    )
    engine.fit(model=model, datamodule=datamodule)
    exported = engine.export(
        model=model,
        export_type="openvino",
        export_root=artifact_dir,
        model_file_name=backend,
        datamodule=datamodule,
    )
    if exported is None or not Path(exported).exists():
        raise RuntimeError(f"{backend} OpenVINO 模型导出失败")
    checkpoint = artifact_dir / f"{backend}.xml"
    exported_path = Path(exported)
    exported_weights = exported_path.with_suffix(".bin")
    if not exported_weights.exists():
        raise RuntimeError(f"{backend} OpenVINO 权重文件不存在")
    if exported_path.resolve() != checkpoint.resolve():
        shutil.move(exported_path, checkpoint)
    weights = artifact_dir / f"{backend}.bin"
    if exported_weights.resolve() != weights.resolve():
        shutil.move(exported_weights, weights)
    checksums = {}
    for model_file_path in (checkpoint, weights):
        with model_file_path.open("rb") as model_file:
            checksums[model_file_path.name] = hashlib.file_digest(model_file, "sha256").hexdigest()
    (artifact_dir / f"{backend}.sha256").write_text(
        json.dumps(checksums, sort_keys=True), encoding="utf-8"
    )
    metrics = {
        "reference_count": len(reference_paths),
        "backend": backend,
        "seed": seed,
        "checkpoint": checkpoint.name,
        "sha256": checksums,
    }
    if backend == "efficientad":
        metrics["auxiliary_data"] = "local_normal_training_set"
        metrics["limit_train_batches"] = parameters.get("limit_train_batches", 1.0)
    return metrics


def _update_job(
    job_id: int,
    model_id: int,
    status: str,
    progress: int,
    message: str,
    started: bool = False,
) -> None:
    with connect() as db:
        db.execute(
            "UPDATE model_versions SET status = ? WHERE id = ?",
            (status, model_id),
        )
        if started:
            db.execute(
                """
                UPDATE training_jobs
                SET status = ?, progress = ?, message = ?, started_at = ? WHERE id = ?
                """,
                (status, progress, message, utc_now(), job_id),
            )
            db.execute(
                "UPDATE experiment_runs SET status = 'running', started_at = ? WHERE model_id = ?",
                (utc_now(), model_id),
            )
        else:
            db.execute(
                "UPDATE training_jobs SET status = ?, progress = ?, message = ? WHERE id = ?",
                (status, progress, message, job_id),
            )
