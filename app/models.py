from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

ID = BigInteger().with_variant(Integer, "sqlite")
JSON_VALUE = JSON().with_variant(JSONB, "postgresql")

products = Table(
    "products", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("threshold", Float, nullable=False, server_default="0.18"),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("roi_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

production_lines = Table(
    "production_lines", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("location", String(200), nullable=False, server_default=""),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

stations = Table(
    "stations", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("line_id", ID, ForeignKey("production_lines.id"), nullable=False),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("camera_identifier", String(120), nullable=False, server_default=""),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("software_version", String(120), nullable=False, server_default=""),
)

reference_images = Table(
    "reference_images", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("path", Text, nullable=False),
    Column("quality_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

datasets = Table(
    "datasets", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE")),
    Column("name", String(120), nullable=False),
    Column("source", String(80), nullable=False, server_default="internal"),
    Column("description", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("product_id", "name"),
)

dataset_versions = Table(
    "dataset_versions", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("dataset_id", ID, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(24), nullable=False, server_default="frozen"),
    Column("manifest_sha256", String(64), nullable=False),
    Column("sample_count", Integer, nullable=False, server_default="0"),
    Column("stats_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_by", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("frozen_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("dataset_id", "version"),
)

dataset_samples = Table(
    "dataset_samples", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("dataset_version_id", ID, ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("object_path", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("label", String(32), nullable=False),
    Column("split", String(24), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("metadata_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("dataset_version_id", "object_path"),
)

model_versions = Table(
    "model_versions", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("backend", String(32), nullable=False),
    Column("status", String(32), nullable=False, server_default="created"),
    Column("artifact_path", Text),
    Column("parameters_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("metrics_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("is_active", Boolean, nullable=False, server_default=text("false")),
    Column("gate_status", String(24), nullable=False, server_default="pending"),
    Column("gate_report_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("published_by", String(120)),
    Column("published_at", DateTime(timezone=True)),
    Column("release_stage", String(24), nullable=False, server_default="candidate"),
    Column("artifact_signature", String(128), nullable=False, server_default=""),
    Column("supersedes_model_id", ID, ForeignKey("model_versions.id", ondelete="SET NULL")),
    Column("dataset_version_id", ID, ForeignKey("dataset_versions.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint("product_id", "version"),
)

experiment_runs = Table(
    "experiment_runs", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("dataset_version_id", ID, ForeignKey("dataset_versions.id", ondelete="SET NULL")),
    Column("model_id", ID, ForeignKey("model_versions.id", ondelete="SET NULL")),
    Column("backend", String(32), nullable=False),
    Column("status", String(24), nullable=False, server_default="queued"),
    Column("parameters_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("metrics_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("code_version", String(120), nullable=False, server_default="unknown"),
    Column("image_digest", String(200), nullable=False, server_default="unknown"),
    Column("created_by", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

training_jobs = Table(
    "training_jobs", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("model_id", ID, ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(32), nullable=False, server_default="queued"),
    Column("progress", Integer, nullable=False, server_default="0"),
    Column("message", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("progress >= 0 AND progress <= 100", name="training_progress"),
)

inspections = Table(
    "inspections", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
    Column("model_id", ID, ForeignKey("model_versions.id", ondelete="SET NULL")),
    Column("station_id", ID, ForeignKey("stations.id", ondelete="SET NULL")),
    Column("filename", String(255), nullable=False),
    Column("image_path", Text, nullable=False),
    Column("heatmap_path", Text, nullable=False),
    Column("anomaly_score", Float, nullable=False),
    Column("decision", String(16), nullable=False),
    Column("review_status", String(16), nullable=False, server_default="pending"),
    Column("reviewed_decision", String(16)),
    Column("review_note", Text, nullable=False, server_default=""),
    Column("lot_number", String(120), nullable=False, server_default=""),
    Column("serial_number", String(120), nullable=False, server_default=""),
    Column("latency_ms", Float, nullable=False, server_default="0"),
    Column("quality_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("shift", String(80), nullable=False, server_default=""),
    Column("defect_json", JSON_VALUE, nullable=False, server_default=text("'[]'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reviewed_at", DateTime(timezone=True)),
)

quality_rules = Table(
    "quality_rules", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE")),
    Column("window_size", Integer, nullable=False, server_default="20"),
    Column("max_anomaly_rate", Float, nullable=False, server_default="0.2"),
    Column("max_consecutive_anomalies", Integer, nullable=False, server_default="3"),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

quality_alerts = Table(
    "quality_alerts", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("rule_id", ID, ForeignKey("quality_rules.id", ondelete="CASCADE"), nullable=False),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
    Column("inspection_id", ID, ForeignKey("inspections.id", ondelete="SET NULL")),
    Column("severity", String(16), nullable=False, server_default="warning"),
    Column("message", Text, nullable=False),
    Column("status", String(24), nullable=False, server_default="open"),
    Column("assigned_to", String(120), nullable=False, server_default=""),
    Column("acknowledged_by", String(120)),
    Column("acknowledge_note", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("closed_at", DateTime(timezone=True)),
)

audit_logs = Table(
    "audit_logs", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("actor", String(120), nullable=False, server_default="system"),
    Column("action", String(120), nullable=False),
    Column("entity_type", String(80), nullable=False),
    Column("entity_id", String(120), nullable=False),
    Column("details_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

integration_events = Table(
    "integration_events", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("event_type", String(120), nullable=False),
    Column("entity_id", String(120), nullable=False),
    Column("payload_json", JSON_VALUE, nullable=False),
    Column("status", String(24), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("external_reference", String(200), nullable=False, server_default=""),
    Column("idempotency_key", String(160), nullable=False, unique=True),
    Column("next_attempt_at", DateTime(timezone=True)),
    Column("locked_at", DateTime(timezone=True)),
    Column("locked_by", String(120), nullable=False, server_default=""),
    Column("last_error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
)

retraining_samples = Table(
    "retraining_samples", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("inspection_id", ID, ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False),
    Column("category", String(32), nullable=False),
    Column("note", Text, nullable=False, server_default=""),
    Column("status", String(24), nullable=False, server_default="queued"),
    Column("created_by", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("inspection_id", "category"),
)

drift_reports = Table(
    "drift_reports", metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("product_id", ID, ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
    Column("model_id", ID, ForeignKey("model_versions.id", ondelete="SET NULL")),
    Column("sample_count", Integer, nullable=False),
    Column("psi", Float, nullable=False),
    Column("mean_shift", Float, nullable=False),
    Column("status", String(24), nullable=False),
    Column("details_json", JSON_VALUE, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

schema_migrations = Table(
    "schema_migrations", metadata,
    Column("version", Integer, primary_key=True),
    Column("description", Text, nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

Index("idx_inspections_product_created", inspections.c.product_id, inspections.c.created_at.desc())
Index("idx_inspections_lot", inspections.c.lot_number)
Index("idx_inspections_serial", inspections.c.product_id, inspections.c.serial_number)
Index("idx_inspections_review", inspections.c.review_status, inspections.c.created_at)
Index("idx_alerts_status", quality_alerts.c.status, quality_alerts.c.created_at.desc())
Index("idx_integration_events_status", integration_events.c.status, integration_events.c.id)
Index("idx_integration_events_due", integration_events.c.status, integration_events.c.next_attempt_at, integration_events.c.id)
Index("idx_drift_product_created", drift_reports.c.product_id, drift_reports.c.created_at.desc())
Index("idx_dataset_versions_dataset", dataset_versions.c.dataset_id, dataset_versions.c.version.desc())
Index("idx_dataset_samples_version_split", dataset_samples.c.dataset_version_id, dataset_samples.c.split)
Index("idx_experiment_runs_created", experiment_runs.c.created_at.desc())
