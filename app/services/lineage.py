from __future__ import annotations

import hashlib
import json

from PIL import Image

from app.database import connect, fetch_all, fetch_one, utc_now
from app.services.data_quality import perceptual_dhash, validate_dataset_samples
from app.services.infrastructure import materialize_file


def snapshot_product_references(
    product_id: int,
    name: str,
    description: str,
    actor: str,
) -> dict:
    references = fetch_all(
        "SELECT filename, path, quality_json FROM reference_images WHERE product_id = ? ORDER BY id",
        (product_id,),
    )
    if not references:
        raise ValueError("没有可冻结的正常参考图")
    samples = []
    for reference in references:
        path = materialize_file(reference["path"])
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        with Image.open(path) as image:
            width, height = image.size
        samples.append({
            "filename": reference["filename"],
            "object_path": reference["path"],
            "sha256": checksum,
            "label": "normal",
            "split": "train",
            "width": width,
            "height": height,
            "metadata": json.loads(reference.get("quality_json") or "{}"),
            "path": path,
            "perceptual_hash": perceptual_dhash(path),
        })
    quality_gate = validate_dataset_samples(samples)
    manifest_payload = json.dumps(
        [{key: sample[key] for key in ("object_path", "sha256", "label", "split")} for sample in samples],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_sha256 = hashlib.sha256(manifest_payload.encode()).hexdigest()
    now = utc_now()
    stats = {
        "labels": {"normal": len(samples)},
        "splits": {"train": len(samples)},
        "total_bytes": sum(materialize_file(item["object_path"]).stat().st_size for item in samples),
        "quality_gate": quality_gate,
    }
    with connect() as db:
        dataset = db.fetch_one(
            "SELECT id FROM datasets WHERE product_id = ? AND name = ?",
            (product_id, name),
        )
        if dataset:
            dataset_id = int(dataset["id"])
        else:
            dataset_id = int(db.execute(
                """INSERT INTO datasets(product_id, name, source, description, created_at)
                   VALUES (?, ?, 'internal', ?, ?) RETURNING id""",
                (product_id, name, description, now),
            ).scalar_one())
        version = int(db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM dataset_versions WHERE dataset_id = ?",
            (dataset_id,),
        ).scalar_one())
        version_id = int(db.execute(
            """INSERT INTO dataset_versions(
                dataset_id, version, status, manifest_sha256, sample_count,
                stats_json, created_by, created_at, frozen_at
            ) VALUES (?, ?, 'frozen', ?, ?, ?, ?, ?, ?) RETURNING id""",
            (
                dataset_id,
                version,
                manifest_sha256,
                len(samples),
                json.dumps(stats),
                actor,
                now,
                now,
            ),
        ).scalar_one())
        for sample in samples:
            db.execute(
                """INSERT INTO dataset_samples(
                    dataset_version_id, filename, object_path, sha256, label, split,
                    width, height, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    sample["filename"],
                    sample["object_path"],
                    sample["sha256"],
                    sample["label"],
                    sample["split"],
                    sample["width"],
                    sample["height"],
                    json.dumps({**sample["metadata"], "perceptual_hash": sample["perceptual_hash"]}),
                    now,
                ),
            )
    return {
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "version": version,
        "status": "frozen",
        "manifest_sha256": manifest_sha256,
        "sample_count": len(samples),
        "stats": stats,
    }


def list_product_datasets(product_id: int) -> list[dict]:
    rows = fetch_all(
        """SELECT d.id AS dataset_id, d.name, d.source, d.description,
                  v.id AS dataset_version_id, v.version, v.status,
                  v.manifest_sha256, v.sample_count, v.stats_json,
                  v.created_by, v.created_at, v.frozen_at
           FROM datasets d JOIN dataset_versions v ON v.dataset_id = d.id
           WHERE d.product_id = ? ORDER BY d.id DESC, v.version DESC""",
        (product_id,),
    )
    for row in rows:
        row["stats"] = json.loads(row.pop("stats_json") or "{}")
    return rows


def dataset_version_or_none(version_id: int | None, product_id: int) -> dict | None:
    if version_id is None:
        return None
    return fetch_one(
        """SELECT v.* FROM dataset_versions v JOIN datasets d ON d.id = v.dataset_id
           WHERE v.id = ? AND d.product_id = ? AND v.status = 'frozen'""",
        (version_id, product_id),
    )


def ensure_dataset_quality(version_id: int, stats: dict) -> dict:
    """Backfill quality gates for dataset versions created before the gate existed."""

    if "quality_gate" in stats:
        return stats
    rows = fetch_all(
        """SELECT filename, object_path, sha256, split, metadata_json
           FROM dataset_samples WHERE dataset_version_id = ? ORDER BY id""",
        (version_id,),
    )
    samples = []
    for row in rows:
        path = materialize_file(row["object_path"])
        metadata = json.loads(row.get("metadata_json") or "{}")
        samples.append(
            {
                "path": path,
                "filename": row["filename"],
                "sha256": row["sha256"],
                "perceptual_hash": metadata.get("perceptual_hash"),
                "split": row["split"],
                "metadata": metadata,
            }
        )
    stats["quality_gate"] = validate_dataset_samples(samples)
    with connect() as db:
        db.execute(
            "UPDATE dataset_versions SET stats_json = ? WHERE id = ?",
            (json.dumps(stats), version_id),
        )
    return stats
