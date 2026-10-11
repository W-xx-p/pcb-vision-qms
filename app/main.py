from __future__ import annotations

import csv
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.middleware.gzip import GZipMiddleware

from app.config import BASE_DIR, DATA_DIR, MEDIA_DIR, ensure_directories
from app.database import (
    DATABASE_BACKEND,
    IS_SQLITE,
    audit,
    connect,
    create_database_dump,
    enqueue_event,
    fetch_all,
    fetch_one,
    init_database,
    utc_now,
)
from app.observability import request_metrics_middleware, runtime_metrics
from app.schemas import (
    AlertAcknowledge,
    AlertWorkflowUpdate,
    CalibrationRequest,
    DatasetSnapshotCreate,
    FeedbackCreate,
    InspectionReview,
    IntegrationEventAcknowledge,
    ModelActivation,
    ModelRollback,
    ModelTrainRequest,
    ProductCreate,
    ProductionLineCreate,
    ProductThresholdUpdate,
    ProductUpdate,
    QualityRuleCreate,
    StationCreate,
    StationHeartbeat,
)
from app.security import (
    Principal,
    current_principal,
    rate_limit_middleware,
    require_api_key,
    require_roles,
    validate_security_config,
)
from app.services.anomaly import get_backend
from app.services.drift import analyze_product_drift
from app.services.evaluation import evaluate_scores, find_best_threshold
from app.services.image_quality import analyze_image_quality
from app.services.infrastructure import (
    enqueue_training,
    infrastructure_status,
    materialize_file,
    materialize_model_dir,
    mirror_file,
)
from app.services.lineage import list_product_datasets, snapshot_product_references
from app.services.model_governance import evaluate_candidate
from app.services.quality_rules import calculate_spc, evaluate_quality_rules
from app.services.storage import media_url, safe_segment, save_image
from app.services.tiling import inspect_tiled
from app.services.training import create_training_job, run_training_job

STATIC_DIR = BASE_DIR / "app" / "static"
ensure_directories()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_security_config()
    ensure_directories()
    init_database()
    yield


app = FastAPI(
    title="PCB 少样本异常检测与质量管理平台",
    version="0.1.0",
    description="面向电子制造质检场景的少样本异常检测、人工复核与质量统计系统。",
    lifespan=lifespan,
)
app.middleware("http")(request_metrics_middleware)
app.middleware("http")(rate_limit_middleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _product_or_404(product_id: int) -> dict:
    product = fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))
    if not product:
        raise HTTPException(404, "产品不存在")
    return product


def _inspection_payload(row: dict) -> dict:
    row["image_url"] = media_url(row.pop("image_path"))
    row["heatmap_url"] = media_url(row.pop("heatmap_path"))
    row["quality"] = json.loads(row.pop("quality_json", "{}") or "{}")
    row["defect_regions"] = json.loads(row.pop("defect_json", "[]") or "[]")
    return row


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/styles.css", include_in_schema=False)
def root_stylesheet() -> FileResponse:
    """Allow the same HTML file to work through FastAPI and direct file preview."""

    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def root_javascript() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


@app.post("/api/auth/session")
def create_browser_session(
    response: Response,
    x_api_key: str | None = Header(default=None),
) -> dict:
    principal = current_principal(x_api_key, None, None)
    if x_api_key:
        response.set_cookie(
            "qms_session",
            x_api_key,
            httponly=True,
            secure=os.getenv("PCB_QMS_SECURE_COOKIES", "0") == "1",
            samesite="strict",
            max_age=8 * 60 * 60,
        )
    return {"actor": principal.name, "role": principal.role}


@app.get("/api/auth/me")
def browser_identity(principal: Principal = Depends(current_principal)) -> dict:
    return {"actor": principal.name, "role": principal.role}


@app.delete("/api/auth/session", status_code=204)
def clear_browser_session(response: Response) -> None:
    response.delete_cookie("qms_session")


@app.get("/media/{file_path:path}", include_in_schema=False)
def protected_media(
    file_path: str,
    _: str = Depends(require_api_key),
) -> FileResponse:
    if ".." in Path(file_path).parts:
        raise HTTPException(400, "媒体路径无效") from None
    try:
        requested = materialize_file(file_path)
    except (FileNotFoundError, OSError):
        raise HTTPException(404, "媒体文件不存在")
    return FileResponse(requested)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "backend": get_backend().name, "version": app.version}


@app.get("/api/ready")
def readiness() -> dict:
    try:
        database = fetch_one("SELECT 1 AS ready")
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, SQLAlchemyError) as exc:
        raise HTTPException(503, f"数据库或存储不可用：{type(exc).__name__}") from None
    infrastructure = infrastructure_status()
    configured_ready = all(
        not item["configured"] or item["ready"] for item in infrastructure.values()
    )
    if not configured_ready:
        raise HTTPException(503, {"status": "not_ready", **infrastructure})
    return {
        "status": "ready",
        "database": database == {"ready": 1},
        "database_backend": DATABASE_BACKEND,
        "storage": True,
        **infrastructure,
    }


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> str:
    return runtime_metrics.prometheus()


@app.get("/api/products")
def list_products(_: str = Depends(require_api_key)) -> list[dict]:
    return fetch_all(
        """
        SELECT p.*,
               COUNT(DISTINCT r.id) AS reference_count,
               COUNT(DISTINCT i.id) AS inspection_count
        FROM products p
        LEFT JOIN reference_images r ON r.product_id = p.id
        LEFT JOIN inspections i ON i.product_id = p.id
        GROUP BY p.id
        ORDER BY p.id DESC
        """
    )


@app.post("/api/products", status_code=201)
def create_product(
    payload: ProductCreate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    try:
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO products(code, name, description, threshold, created_at)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    payload.code.upper(),
                    payload.name.strip(),
                    payload.description.strip(),
                    payload.threshold,
                    utc_now(),
                ),
            )
            product_id = cursor.scalar_one()
    except IntegrityError:
        raise HTTPException(409, "产品编码已经存在") from None
    audit("product.created", "product", int(product_id), payload.model_dump(), actor)
    return _product_or_404(int(product_id))


@app.get("/api/products/{product_id}")
def get_product(product_id: int, _: str = Depends(require_api_key)) -> dict:
    product = _product_or_404(product_id)
    product["references"] = [
        {
            **row,
            "quality": json.loads(row.pop("quality_json", "{}") or "{}"),
            "url": media_url(row["path"]),
        }
        for row in fetch_all(
            "SELECT * FROM reference_images WHERE product_id = ? ORDER BY id DESC",
            (product_id,),
        )
    ]
    return product


@app.patch("/api/products/{product_id}/threshold")
def update_threshold(
    product_id: int,
    payload: ProductThresholdUpdate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    with connect() as db:
        db.execute("UPDATE products SET threshold = ? WHERE id = ?", (payload.threshold, product_id))
    audit("product.threshold_updated", "product", product_id, payload.model_dump(), actor)
    return _product_or_404(product_id)


@app.patch("/api/products/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return _product_or_404(product_id)
    assignments, params = [], []
    for field in ("name", "description", "enabled"):
        if field in changes:
            assignments.append(f"{field} = ?")
            params.append(bool(changes[field]) if field == "enabled" else changes[field].strip())
    if "roi" in changes:
        assignments.append("roi_json = ?")
        params.append(json.dumps(changes["roi"], ensure_ascii=False))
    assignments.append("updated_at = ?")
    params.extend([utc_now(), product_id])
    with connect() as db:
        db.execute(f"UPDATE products SET {', '.join(assignments)} WHERE id = ?", tuple(params))
    audit("product.updated", "product", product_id, changes, actor)
    return _product_or_404(product_id)


@app.post("/api/products/{product_id}/references", status_code=201)
async def upload_references(
    product_id: int,
    files: list[UploadFile] = File(...),
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    product = _product_or_404(product_id)
    if not files or len(files) > 50:
        raise HTTPException(400, "每次需要上传 1 到 50 张正常参考图片")

    folder = MEDIA_DIR / "references" / safe_segment(product["code"])
    created = []
    for upload in files:
        path, original_name = await save_image(upload, folder)
        object_key = mirror_file(path)
        quality = analyze_image_quality(path).as_dict()
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO reference_images(product_id, filename, path, created_at, quality_json)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                """,
                (product_id, original_name, object_key or str(path), utc_now(), json.dumps(quality, ensure_ascii=False)),
            )
            reference_id = cursor.scalar_one()
        created.append(
            {"id": reference_id, "filename": original_name, "url": media_url(object_key or path), "quality": quality}
        )
        if object_key:
            path.unlink(missing_ok=True)
    audit(
        "references.uploaded",
        "product",
        product_id,
        {"count": len(created), "filenames": [item["filename"] for item in created]},
        actor,
    )
    return {"created": created, "reference_count": len(created)}


@app.post("/api/products/{product_id}/datasets/snapshot", status_code=201)
def create_dataset_snapshot(
    product_id: int,
    payload: DatasetSnapshotCreate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    try:
        result = snapshot_product_references(
            product_id,
            payload.name.strip(),
            payload.description.strip(),
            actor,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit("dataset.snapshot_created", "dataset_version", result["dataset_version_id"], result, actor)
    return result


@app.get("/api/products/{product_id}/datasets")
def list_datasets(product_id: int, _: str = Depends(require_api_key)) -> list[dict]:
    _product_or_404(product_id)
    return list_product_datasets(product_id)


@app.get("/api/experiments")
def list_experiments(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_roles("admin", "engineer", "auditor")),
) -> list[dict]:
    rows = fetch_all(
        """SELECT e.*, m.product_id, m.version AS model_version,
                  v.manifest_sha256 AS dataset_manifest_sha256
           FROM experiment_runs e
           LEFT JOIN model_versions m ON m.id = e.model_id
           LEFT JOIN dataset_versions v ON v.id = e.dataset_version_id
           ORDER BY e.id DESC LIMIT ?""",
        (limit,),
    )
    for row in rows:
        row["parameters"] = json.loads(row.pop("parameters_json") or "{}")
        row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
    return rows


@app.delete("/api/products/{product_id}/references/{reference_id}")
def delete_reference(
    product_id: int,
    reference_id: int,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    reference = fetch_one(
        "SELECT * FROM reference_images WHERE id = ? AND product_id = ?",
        (reference_id, product_id),
    )
    if not reference:
        raise HTTPException(404, "参考图片不存在")
    with connect() as db:
        db.execute("DELETE FROM reference_images WHERE id = ?", (reference_id,))
    Path(reference["path"]).unlink(missing_ok=True)
    audit("reference.deleted", "reference", reference_id, {"product_id": product_id}, actor)
    return {"deleted": reference_id}


@app.post("/api/products/{product_id}/models/train", status_code=202)
def train_model(
    product_id: int,
    payload: ModelTrainRequest,
    background_tasks: BackgroundTasks,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    reference_count = fetch_one(
        "SELECT COUNT(*) AS count FROM reference_images WHERE product_id = ?",
        (product_id,),
    )["count"]
    if reference_count < 1:
        raise HTTPException(409, "请先上传正常参考图片")
    parameters = payload.model_dump(exclude={"backend"})
    try:
        model_id, job_id = create_training_job(
            product_id, payload.backend, parameters, actor=actor
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    queued_externally = enqueue_training(job_id)
    if not queued_externally:
        background_tasks.add_task(run_training_job, job_id)
    audit(
        "model.training_requested",
        "model",
        model_id,
        {"job_id": job_id, "backend": payload.backend, **parameters},
        actor,
    )
    return {
        "model_id": model_id,
        "job_id": job_id,
        "status": "queued",
        "execution": "redis-worker" if queued_externally else "in-process",
    }


@app.get("/api/products/{product_id}/models")
def list_models(product_id: int, _: str = Depends(require_api_key)) -> list[dict]:
    _product_or_404(product_id)
    models = fetch_all(
        "SELECT * FROM model_versions WHERE product_id = ? ORDER BY version DESC",
        (product_id,),
    )
    for model in models:
        model["parameters"] = json.loads(model.pop("parameters_json"))
        model["metrics"] = json.loads(model.pop("metrics_json"))
        model["is_active"] = bool(model["is_active"])
    return models


@app.post("/api/products/{product_id}/drift/scan")
def scan_product_drift(
    product_id: int,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    result = analyze_product_drift(product_id)
    audit("model.drift_scanned", "product", product_id, result, actor)
    return result


@app.get("/api/products/{product_id}/drift")
def list_product_drift(product_id: int, _: str = Depends(require_api_key)) -> list[dict]:
    _product_or_404(product_id)
    rows = fetch_all("SELECT * FROM drift_reports WHERE product_id = ? ORDER BY id DESC LIMIT 100", (product_id,))
    for row in rows:
        row["details"] = json.loads(row.pop("details_json", "{}"))
    return rows


@app.get("/api/training-jobs/{job_id}")
def get_training_job(job_id: int, _: str = Depends(require_api_key)) -> dict:
    job = fetch_one(
        """
        SELECT j.*, m.product_id, m.version, m.backend
        FROM training_jobs j JOIN model_versions m ON m.id = j.model_id
        WHERE j.id = ?
        """,
        (job_id,),
    )
    if not job:
        raise HTTPException(404, "训练任务不存在")
    return job


@app.post("/api/products/{product_id}/models/activate")
def activate_model(
    product_id: int,
    payload: ModelActivation,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    model = fetch_one(
        "SELECT * FROM model_versions WHERE id = ? AND product_id = ?",
        (payload.model_id, product_id),
    )
    if not model:
        raise HTTPException(404, "模型版本不存在")
    if model["status"] != "ready":
        raise HTTPException(409, "只有训练完成的模型可以启用")
    if model.get("gate_status") != "passed":
        raise HTTPException(409, "模型未通过独立评估发布门禁，禁止上线")
    gate_report = json.loads(model.get("gate_report_json", "{}") or "{}")
    recommended_threshold = gate_report.get("metrics", {}).get("threshold")
    with connect() as db:
        previous = db.fetch_one(
            "SELECT id FROM model_versions WHERE product_id = ? AND is_active = TRUE",
            (product_id,),
        )
        db.execute(
            "UPDATE model_versions SET is_active = FALSE, release_stage = 'retired' WHERE product_id = ?",
            (product_id,),
        )
        db.execute(
            """UPDATE model_versions SET is_active = TRUE, release_stage = 'active',
               supersedes_model_id = ?, published_by = ?, published_at = ? WHERE id = ?""",
            (previous["id"] if previous else None, actor, utc_now(), payload.model_id),
        )
        if recommended_threshold is not None:
            db.execute("UPDATE products SET threshold = ?, updated_at = ? WHERE id = ?", (recommended_threshold, utc_now(), product_id))
        enqueue_event(
            "model.activated",
            payload.model_id,
            {"product_id": product_id, "model_id": payload.model_id},
            db=db,
        )
        audit("model.activated", "model", payload.model_id, {"product_id": product_id}, actor, db=db)
    return {"active_model_id": payload.model_id}


@app.post("/api/products/{product_id}/models/rollback")
def rollback_model(
    product_id: int,
    payload: ModelRollback,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    active = fetch_one(
        "SELECT * FROM model_versions WHERE product_id = ? AND is_active = TRUE",
        (product_id,),
    )
    if not active:
        raise HTTPException(409, "当前产品没有活动模型")
    target_id = payload.target_model_id or active.get("supersedes_model_id")
    if not target_id:
        raise HTTPException(409, "当前模型没有可回滚的前序版本")
    target = fetch_one(
        """SELECT * FROM model_versions WHERE id = ? AND product_id = ?
           AND status = 'ready' AND gate_status = 'passed'""",
        (target_id, product_id),
    )
    if not target:
        raise HTTPException(409, "目标模型不存在、未完成训练或未通过发布门禁")
    with connect() as db:
        db.execute(
            "UPDATE model_versions SET is_active = FALSE, release_stage = 'rolled_back' WHERE id = ?",
            (active["id"],),
        )
        db.execute(
            """UPDATE model_versions SET is_active = TRUE, release_stage = 'active',
               published_by = ?, published_at = ? WHERE id = ?""",
            (actor, utc_now(), target_id),
        )
        enqueue_event(
            "model.rolled_back",
            active["id"],
            {
                "product_id": product_id,
                "from_model_id": active["id"],
                "to_model_id": target_id,
                "reason": payload.reason,
            },
            db=db,
        )
        audit(
            "model.rolled_back",
            "model",
            active["id"],
            {"to_model_id": target_id, "reason": payload.reason},
            actor,
            db=db,
        )
    return {"rolled_back_model_id": active["id"], "active_model_id": target_id}


@app.post("/api/products/{product_id}/models/{model_id}/evaluate")
def evaluate_model_gate(
    product_id: int,
    model_id: int,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    model = fetch_one("SELECT * FROM model_versions WHERE id = ? AND product_id = ? AND status = 'ready'", (model_id, product_id))
    if not model or not model.get("artifact_path"):
        raise HTTPException(404, "候选模型不存在或尚未训练完成")
    artifact = materialize_model_dir(model["artifact_path"], model["backend"])
    report = evaluate_candidate(product_id, model["backend"], artifact)
    with connect() as db:
        db.execute(
            """UPDATE model_versions SET gate_status = ?, gate_report_json = ?,
               release_stage = ? WHERE id = ?""",
            (
                "passed" if report["passed"] else "blocked",
                json.dumps(report),
                "approved" if report["passed"] else "rejected",
                model_id,
            ),
        )
        audit("model.gate_evaluated", "model", model_id, report, actor, db=db)
    return report


@app.post("/api/products/{product_id}/inspect", status_code=201)
async def inspect_product(
    product_id: int,
    file: UploadFile = File(...),
    lot_number: str = Form(default=""),
    serial_number: str = Form(default=""),
    station_code: str = Form(default=""),
    shift: str = Form(default=""),
    actor: str = Depends(require_roles("admin", "engineer", "inspector")),
) -> dict:
    product = _product_or_404(product_id)
    station = None
    if station_code.strip():
        station = fetch_one(
            "SELECT * FROM stations WHERE code = ? AND enabled = TRUE",
            (station_code.strip().upper(),),
        )
        if not station:
            raise HTTPException(400, "工位不存在或已停用")
    clean_serial = serial_number.strip()[:120]
    if clean_serial and fetch_one(
        "SELECT id FROM inspections WHERE product_id = ? AND serial_number = ?",
        (product_id, clean_serial),
    ):
        raise HTTPException(409, "该产品序列号已经检测，禁止重复过站")
    reference_rows = fetch_all(
        "SELECT path FROM reference_images WHERE product_id = ? ORDER BY id", (product_id,)
    )
    if not reference_rows:
        raise HTTPException(409, "请先为该产品上传正常参考图片")

    image_folder = MEDIA_DIR / "inspections" / safe_segment(product["code"])
    image_path, original_name = await save_image(file, image_folder)
    quality = analyze_image_quality(image_path).as_dict()
    heatmap_path = MEDIA_DIR / "heatmaps" / safe_segment(product["code"]) / f"{image_path.stem}.jpg"

    active_model = fetch_one(
        """
        SELECT * FROM model_versions
        WHERE product_id = ? AND is_active = TRUE AND status = 'ready'
        ORDER BY version DESC LIMIT 1
        """,
        (product_id,),
    )
    backend_name = active_model["backend"] if active_model else "reference"
    artifact_path = (
        materialize_model_dir(active_model["artifact_path"], backend_name)
        if active_model and active_model["artifact_path"]
        else None
    )
    started = perf_counter()
    try:
        backend = get_backend(backend_name)
        references = [materialize_file(row["path"]) for row in reference_rows]
        roi = json.loads(product.get("roi_json", "{}") or "{}")
        model_parameters = (
            json.loads(active_model.get("parameters_json", "{}") or "{}")
            if active_model
            else {}
        )
        if active_model and model_parameters.get("tiling_enabled"):
            result = inspect_tiled(
                backend=backend,
                image_path=image_path,
                reference_paths=references,
                threshold=float(product["threshold"]),
                heatmap_path=heatmap_path,
                artifact_path=artifact_path,
                roi=roi,
                tile_size=int(model_parameters.get("tile_size", 512)),
                overlap=float(model_parameters.get("tile_overlap", 0.2)),
                batch_size=int(model_parameters.get("tile_batch_size", 8)),
            )
        else:
            result = backend.inspect(
                image_path=image_path,
                reference_paths=references,
                threshold=float(product["threshold"]),
                heatmap_path=heatmap_path,
                artifact_path=artifact_path,
                roi=roi,
            )
    except (ValueError, RuntimeError) as exc:
        image_path.unlink(missing_ok=True)
        raise HTTPException(503, str(exc)) from None
    latency_ms = round((perf_counter() - started) * 1000, 2)
    image_key = mirror_file(image_path)
    heatmap_key = mirror_file(result.heatmap_path)

    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO inspections(
                product_id, filename, image_path, heatmap_path, anomaly_score,
                decision, review_status, created_at, model_id, lot_number,
                serial_number, latency_ms, quality_json, station_id, shift, defect_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                product_id,
                original_name,
                image_key or str(image_path),
                heatmap_key or str(result.heatmap_path),
                result.score,
                result.decision,
                utc_now(),
                active_model["id"] if active_model else None,
                lot_number.strip()[:120],
                clean_serial,
                latency_ms,
                json.dumps(quality, ensure_ascii=False),
                station["id"] if station else None,
                shift.strip()[:40],
                json.dumps(result.regions, ensure_ascii=False),
            ),
        )
        inspection_id = cursor.scalar_one()
        row = db.fetch_one(
            """
            SELECT i.*, p.code AS product_code, p.name AS product_name
            FROM inspections i JOIN products p ON p.id = i.product_id
            WHERE i.id = ?
            """,
            (inspection_id,),
        )
        audit(
            "inspection.created",
            "inspection",
            inspection_id,
            {"decision": result.decision, "score": result.score, "backend": backend_name},
            actor,
            db=db,
        )
        integration_event_id = enqueue_event(
            "inspection.completed",
            int(inspection_id),
            {
                "inspection_id": int(inspection_id),
                "product_id": product_id,
                "product_code": product["code"],
                "lot_number": lot_number.strip()[:120],
                "serial_number": clean_serial,
                "station_code": station["code"] if station else "",
                "shift": shift.strip()[:40],
                "decision": result.decision,
                "anomaly_score": result.score,
                "model_id": active_model["id"] if active_model else None,
                "created_at": row["created_at"],
            },
            db=db,
        )
        alert_ids = evaluate_quality_rules(product_id, int(inspection_id), db=db)
    if image_key:
        image_path.unlink(missing_ok=True)
    if heatmap_key:
        result.heatmap_path.unlink(missing_ok=True)
    payload = _inspection_payload(row)
    payload["triggered_alert_ids"] = alert_ids
    payload["integration_event_id"] = integration_event_id
    return payload


@app.post("/api/products/{product_id}/batch-inspect", status_code=201)
async def batch_inspect_product(
    product_id: int,
    files: list[UploadFile] = File(...),
    lot_number: str = Form(default=""),
    station_code: str = Form(default=""),
    shift: str = Form(default=""),
    actor: str = Depends(require_roles("admin", "engineer", "inspector")),
) -> dict:
    if not files or len(files) > 100:
        raise HTTPException(400, "每个批次需要上传 1 到 100 张图片")
    results = []
    failures = []
    for upload in files:
        try:
            result = await inspect_product(
                product_id=product_id,
                file=upload,
                lot_number=lot_number,
                serial_number="",
                station_code=station_code,
                shift=shift,
                actor=actor,
            )
            results.append(result)
        except HTTPException as exc:
            failures.append({"filename": upload.filename, "error": str(exc.detail)})
    return {
        "lot_number": lot_number,
        "processed": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }


@app.get("/api/inspections")
def list_inspections(
    product_id: int | None = None,
    decision: str | None = Query(default=None, pattern=r"^(normal|anomaly)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    lot_number: str | None = Query(default=None, max_length=120),
    serial_number: str | None = Query(default=None, max_length=120),
    review_status: str | None = Query(default=None, pattern=r"^(pending|reviewed)$"),
    _: str = Depends(require_api_key),
) -> list[dict]:
    where = []
    params: list[object] = []
    if product_id is not None:
        where.append("i.product_id = ?")
        params.append(product_id)
    if decision:
        where.append("i.decision = ?")
        params.append(decision)
    if lot_number:
        where.append("i.lot_number = ?")
        params.append(lot_number)
    if serial_number:
        where.append("i.serial_number = ?")
        params.append(serial_number)
    if review_status:
        where.append("i.review_status = ?")
        params.append(review_status)
    clause = "WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    rows = fetch_all(
        f"""
        SELECT i.*, p.code AS product_code, p.name AS product_name
        FROM inspections i JOIN products p ON p.id = i.product_id
        {clause}
        ORDER BY i.id DESC LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    return [_inspection_payload(row) for row in rows]


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: int, _: str = Depends(require_api_key)) -> dict:
    row = fetch_one(
        """
        SELECT i.*, p.code AS product_code, p.name AS product_name,
               s.code AS station_code, l.code AS line_code
        FROM inspections i JOIN products p ON p.id = i.product_id
        LEFT JOIN stations s ON s.id = i.station_id
        LEFT JOIN production_lines l ON l.id = s.line_id
        WHERE i.id = ?
        """,
        (inspection_id,),
    )
    if not row:
        raise HTTPException(404, "检测记录不存在")
    return _inspection_payload(row)


@app.patch("/api/inspections/{inspection_id}/review")
def review_inspection(
    inspection_id: int,
    payload: InspectionReview,
    actor: str = Depends(require_roles("admin", "engineer", "reviewer")),
) -> dict:
    existing = fetch_one("SELECT * FROM inspections WHERE id = ?", (inspection_id,))
    if not existing:
        raise HTTPException(404, "检测记录不存在")
    with connect() as db:
        db.execute(
            """
            UPDATE inspections
            SET review_status = 'reviewed', reviewed_decision = ?, review_note = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (payload.decision, payload.note.strip(), utc_now(), inspection_id),
        )
    row = fetch_one(
        """
        SELECT i.*, p.code AS product_code, p.name AS product_name
        FROM inspections i JOIN products p ON p.id = i.product_id
        WHERE i.id = ?
        """,
        (inspection_id,),
    )
    audit("inspection.reviewed", "inspection", inspection_id, payload.model_dump(), actor)
    return _inspection_payload(row)


@app.get("/api/dashboard")
def dashboard(_: str = Depends(require_api_key)) -> dict:
    totals = fetch_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN decision = 'anomaly' THEN 1 ELSE 0 END) AS anomalies,
               SUM(CASE WHEN review_status = 'pending' THEN 1 ELSE 0 END) AS pending_reviews,
               AVG(anomaly_score) AS average_score
        FROM inspections
        """
    )
    product_count = fetch_one("SELECT COUNT(*) AS count FROM products")["count"]
    total = int(totals["total"] or 0)
    anomalies = int(totals["anomalies"] or 0)
    return {
        "product_count": product_count,
        "inspection_count": total,
        "anomaly_count": anomalies,
        "anomaly_rate": round(anomalies / total, 4) if total else 0,
        "pending_reviews": int(totals["pending_reviews"] or 0),
        "average_score": round(float(totals["average_score"] or 0), 4),
    }


@app.get("/api/products/{product_id}/evaluation")
def evaluate_product(product_id: int, _: str = Depends(require_api_key)) -> dict:
    product = _product_or_404(product_id)
    samples = [
        (float(row["anomaly_score"]), row["reviewed_decision"])
        for row in fetch_all(
            """
            SELECT anomaly_score, reviewed_decision FROM inspections
            WHERE product_id = ? AND review_status = 'reviewed'
              AND reviewed_decision IN ('normal', 'anomaly')
            """,
            (product_id,),
        )
    ]
    if not samples:
        return {
            "ready": False,
            "message": "至少需要已复核的正常和异常样本才能评估",
            "sample_count": 0,
        }
    metrics = evaluate_scores(samples, float(product["threshold"]))
    return {"ready": True, **metrics.as_dict()}


@app.post("/api/products/{product_id}/calibrate-threshold")
def calibrate_product_threshold(
    product_id: int,
    payload: CalibrationRequest,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    _product_or_404(product_id)
    samples = [
        (float(row["anomaly_score"]), row["reviewed_decision"])
        for row in fetch_all(
            """
            SELECT anomaly_score, reviewed_decision FROM inspections
            WHERE product_id = ? AND review_status = 'reviewed'
              AND reviewed_decision IN ('normal', 'anomaly')
            """,
            (product_id,),
        )
    ]
    normal_count = sum(label == "normal" for _, label in samples)
    anomaly_count = sum(label == "anomaly" for _, label in samples)
    if normal_count < 2 or anomaly_count < 2:
        raise HTTPException(409, "自动校准至少需要2个已复核正常样本和2个异常样本")
    best = find_best_threshold(samples)
    if payload.apply_threshold:
        with connect() as db:
            db.execute(
                "UPDATE products SET threshold = ? WHERE id = ?",
                (best.threshold, product_id),
            )
        audit(
            "product.threshold_calibrated",
            "product",
            product_id,
            best.as_dict(),
            actor,
        )
    return {"applied": payload.apply_threshold, **best.as_dict()}


@app.get("/api/products/{product_id}/quality-trend")
def product_quality_trend(
    product_id: int,
    days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(require_api_key),
) -> list[dict]:
    _product_or_404(product_id)
    return fetch_all(
        """
        SELECT SUBSTR(created_at, 1, 10) AS day,
               COUNT(*) AS inspection_count,
               SUM(CASE WHEN decision = 'anomaly' THEN 1 ELSE 0 END) AS anomaly_count,
               ROUND(AVG(anomaly_score), 4) AS average_score,
               ROUND(AVG(latency_ms), 2) AS average_latency_ms
        FROM inspections
        WHERE product_id = ?
        GROUP BY SUBSTR(created_at, 1, 10)
        ORDER BY day DESC
        LIMIT ?
        """,
        (product_id, days),
    )


@app.get("/api/reports/inspections.csv")
def export_inspections_csv(
    product_id: int | None = None,
    actor: str = Depends(require_api_key),
) -> StreamingResponse:
    clause = "WHERE i.product_id = ?" if product_id is not None else ""
    params = (product_id,) if product_id is not None else ()
    rows = fetch_all(
        f"""
        SELECT i.id, i.created_at, p.code AS product_code, p.name AS product_name,
               i.filename, i.lot_number, i.serial_number, i.anomaly_score,
               i.decision, i.review_status, i.reviewed_decision, i.review_note,
               i.latency_ms
        FROM inspections i JOIN products p ON p.id = i.product_id
        {clause}
        ORDER BY i.id DESC
        """,
        params,
    )
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("id,created_at,product_code,decision\n")
    audit("report.exported", "inspection_report", product_id or "all", {"rows": len(rows)}, actor)
    content = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=inspection-report.csv"},
    )


@app.get("/api/audit-logs")
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_roles("admin", "auditor")),
) -> list[dict]:
    logs = fetch_all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
    for log in logs:
        log["details"] = json.loads(log.pop("details_json"))
    return logs


@app.post("/api/production-lines", status_code=201)
def create_production_line(
    payload: ProductionLineCreate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    try:
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO production_lines(code, name, location, created_at)
                VALUES (?, ?, ?, ?) RETURNING id
                """,
                (payload.code.upper(), payload.name.strip(), payload.location.strip(), utc_now()),
            )
            line_id = int(cursor.scalar_one())
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "产线编码已经存在") from None
        raise
    audit("production_line.created", "production_line", line_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM production_lines WHERE id = ?", (line_id,))


@app.get("/api/production-lines")
def list_production_lines(_: str = Depends(require_api_key)) -> list[dict]:
    return fetch_all(
        """
        SELECT l.*, COUNT(s.id) AS station_count
        FROM production_lines l LEFT JOIN stations s ON s.line_id = l.id
        GROUP BY l.id ORDER BY l.id DESC
        """
    )


@app.post("/api/stations", status_code=201)
def create_station(
    payload: StationCreate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    if not fetch_one("SELECT id FROM production_lines WHERE id = ?", (payload.line_id,)):
        raise HTTPException(404, "产线不存在")
    try:
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO stations(line_id, code, name, camera_identifier, created_at)
                VALUES (?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    payload.line_id,
                    payload.code.upper(),
                    payload.name.strip(),
                    payload.camera_identifier.strip(),
                    utc_now(),
                ),
            )
            station_id = int(cursor.scalar_one())
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "工位编码已经存在") from None
        raise
    audit("station.created", "station", station_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM stations WHERE id = ?", (station_id,))


@app.get("/api/stations")
def list_stations(
    line_id: int | None = None,
    _: str = Depends(require_api_key),
) -> list[dict]:
    clause = "WHERE s.line_id = ?" if line_id else ""
    params = (line_id,) if line_id else ()
    return fetch_all(
        f"""
        SELECT s.*, l.code AS line_code, l.name AS line_name
        FROM stations s JOIN production_lines l ON l.id = s.line_id
        {clause} ORDER BY s.id DESC
        """,
        params,
    )


@app.post("/api/stations/{station_id}/heartbeat")
def station_heartbeat(
    station_id: int,
    payload: StationHeartbeat,
    actor: str = Depends(require_roles("admin", "engineer", "inspector", "integration")),
) -> dict:
    if not fetch_one("SELECT id FROM stations WHERE id = ?", (station_id,)):
        raise HTTPException(404, "工位不存在")
    now = utc_now()
    with connect() as db:
        db.execute(
            "UPDATE stations SET last_seen_at = ?, software_version = ? WHERE id = ?",
            (now, payload.software_version.strip(), station_id),
        )
    return {"station_id": station_id, "status": "online", "last_seen_at": now, "actor": actor}


@app.post("/api/quality-rules", status_code=201)
def create_quality_rule(
    payload: QualityRuleCreate,
    actor: str = Depends(require_roles("admin", "engineer")),
) -> dict:
    if payload.product_id is not None:
        _product_or_404(payload.product_id)
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO quality_rules(
                name, product_id, window_size, max_anomaly_rate,
                max_consecutive_anomalies, created_at
            ) VALUES (?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                payload.name.strip(),
                payload.product_id,
                payload.window_size,
                payload.max_anomaly_rate,
                payload.max_consecutive_anomalies,
                utc_now(),
            ),
        )
        rule_id = int(cursor.scalar_one())
    audit("quality_rule.created", "quality_rule", rule_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM quality_rules WHERE id = ?", (rule_id,))


@app.get("/api/quality-rules")
def list_quality_rules(_: str = Depends(require_api_key)) -> list[dict]:
    return fetch_all("SELECT * FROM quality_rules ORDER BY id DESC")


@app.get("/api/quality-alerts")
def list_quality_alerts(
    status: str | None = Query(default=None, pattern=r"^(open|acknowledged)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_api_key),
) -> list[dict]:
    clause = "WHERE a.status = ?" if status else ""
    params: tuple[object, ...] = (status, limit) if status else (limit,)
    return fetch_all(
        f"""
        SELECT a.*, r.name AS rule_name, p.code AS product_code
        FROM quality_alerts a
        JOIN quality_rules r ON r.id = a.rule_id
        JOIN products p ON p.id = a.product_id
        {clause} ORDER BY a.id DESC LIMIT ?
        """,
        params,
    )


@app.patch("/api/quality-alerts/{alert_id}/acknowledge")
def acknowledge_quality_alert(
    alert_id: int,
    payload: AlertAcknowledge,
    actor: str = Depends(require_roles("admin", "engineer", "reviewer")),
) -> dict:
    if not fetch_one("SELECT id FROM quality_alerts WHERE id = ?", (alert_id,)):
        raise HTTPException(404, "质量告警不存在")
    with connect() as db:
        db.execute(
            """
            UPDATE quality_alerts SET status = 'acknowledged', acknowledged_by = ?,
                acknowledge_note = ?, acknowledged_at = ? WHERE id = ?
            """,
            (actor, payload.note.strip(), utc_now(), alert_id),
        )
    audit("quality_alert.acknowledged", "quality_alert", alert_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM quality_alerts WHERE id = ?", (alert_id,))


@app.patch("/api/quality-alerts/{alert_id}/workflow")
def update_quality_alert_workflow(
    alert_id: int,
    payload: AlertWorkflowUpdate,
    actor: str = Depends(require_roles("admin", "engineer", "reviewer")),
) -> dict:
    if not fetch_one("SELECT id FROM quality_alerts WHERE id = ?", (alert_id,)):
        raise HTTPException(404, "质量告警不存在")
    now = utc_now()
    with connect() as db:
        db.execute(
            """
            UPDATE quality_alerts SET status = ?, assigned_to = ?, acknowledge_note = ?,
                acknowledged_by = ?, acknowledged_at = ?, closed_at = ? WHERE id = ?
            """,
            (
                payload.status,
                payload.assigned_to.strip(),
                payload.note.strip(),
                actor,
                now if payload.status != "open" else None,
                now if payload.status == "closed" else None,
                alert_id,
            ),
        )
    audit("quality_alert.workflow_updated", "quality_alert", alert_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM quality_alerts WHERE id = ?", (alert_id,))


@app.post("/api/inspections/{inspection_id}/feedback", status_code=201)
def create_retraining_feedback(
    inspection_id: int,
    payload: FeedbackCreate,
    actor: str = Depends(require_roles("admin", "engineer", "reviewer")),
) -> dict:
    if not fetch_one("SELECT id FROM inspections WHERE id = ?", (inspection_id,)):
        raise HTTPException(404, "检测记录不存在")
    try:
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO retraining_samples(
                    inspection_id, category, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?) RETURNING id
                """,
                (inspection_id, payload.category, payload.note.strip(), actor, utc_now()),
            )
            sample_id = int(cursor.scalar_one())
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "该检测记录已经加入同类回流队列") from None
        raise
    audit("retraining_sample.created", "inspection", inspection_id, payload.model_dump(), actor)
    return fetch_one("SELECT * FROM retraining_samples WHERE id = ?", (sample_id,))


@app.get("/api/products/{product_id}/spc")
def product_spc(
    product_id: int,
    limit: int = Query(default=100, ge=5, le=1000),
    _: str = Depends(require_api_key),
) -> dict:
    _product_or_404(product_id)
    return calculate_spc(product_id, limit)


@app.get("/api/integration/events")
def list_integration_events(
    status: str = Query(default="pending", pattern=r"^(pending|processing|retrying|failed|delivered)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_roles("admin", "integration")),
) -> list[dict]:
    events = fetch_all(
        "SELECT * FROM integration_events WHERE status = ? ORDER BY id LIMIT ?",
        (status, limit),
    )
    for event in events:
        event["payload"] = json.loads(event.pop("payload_json"))
    return events


@app.patch("/api/integration/events/{event_id}/delivered")
def mark_integration_event_delivered(
    event_id: int,
    payload: IntegrationEventAcknowledge,
    actor: str = Depends(require_roles("admin", "integration")),
) -> dict:
    if not fetch_one("SELECT id FROM integration_events WHERE id = ?", (event_id,)):
        raise HTTPException(404, "集成事件不存在")
    with connect() as db:
        db.execute(
            """
            UPDATE integration_events SET status = 'delivered', attempts = attempts + 1,
                external_reference = ?, delivered_at = ? WHERE id = ?
            """,
            (payload.external_reference.strip(), utc_now(), event_id),
        )
    audit("integration_event.delivered", "integration_event", event_id, payload.model_dump(), actor)
    return {"id": event_id, "status": "delivered"}


@app.post("/api/admin/backups", status_code=201)
def create_database_backup(actor: str = Depends(require_roles("admin"))) -> dict:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".db" if IS_SQLITE else ".dump"
    filename = f"quality-{utc_now().replace(':', '').replace('+', '_')}{suffix}"
    destination = backup_dir / filename
    try:
        create_database_dump(destination)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    audit("database.backup_created", "database", filename, {"bytes": destination.stat().st_size}, actor)
    return {"filename": filename, "bytes": destination.stat().st_size, "download_url": f"/api/admin/backups/{filename}"}


@app.get("/api/admin/backups/{filename}")
def download_database_backup(
    filename: str,
    _: str = Depends(require_roles("admin")),
) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith((".db", ".dump")):
        raise HTTPException(400, "备份文件名无效")
    path = DATA_DIR / "backups" / safe_name
    if not path.is_file():
        raise HTTPException(404, "备份不存在")
    return FileResponse(path, filename=safe_name, media_type="application/octet-stream")
