"""Add immutable dataset versions and experiment lineage.

Revision ID: 20261007_0004
Revises: 20261007_0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261007_0004"
down_revision = "20261007_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # See revision 0002: fresh databases created by the legacy dynamic baseline
    # already have these objects; real 0003 installations receive the full delta.
    inspector = sa.inspect(op.get_bind())
    if "datasets" in inspector.get_table_names():
        return
    op.create_table(
        "datasets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source", sa.String(80), nullable=False, server_default="internal"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "name", name="uq_datasets_product_id"),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_id", sa.BigInteger(), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="frozen"),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_id"),
    )
    op.create_table(
        "dataset_samples",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_version_id", sa.BigInteger(), sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("object_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("split", sa.String(24), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_version_id", "object_path", name="uq_dataset_samples_dataset_version_id"),
    )
    op.add_column("model_versions", sa.Column("dataset_version_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_model_versions_dataset_version_id_dataset_versions", "model_versions", "dataset_versions", ["dataset_version_id"], ["id"], ondelete="SET NULL")
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_version_id", sa.BigInteger(), sa.ForeignKey("dataset_versions.id", ondelete="SET NULL")),
        sa.Column("model_id", sa.BigInteger(), sa.ForeignKey("model_versions.id", ondelete="SET NULL")),
        sa.Column("backend", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("parameters_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("code_version", sa.String(120), nullable=False, server_default="unknown"),
        sa.Column("image_digest", sa.String(200), nullable=False, server_default="unknown"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_dataset_versions_dataset", "dataset_versions", ["dataset_id", sa.text("version DESC")])
    op.create_index("idx_dataset_samples_version_split", "dataset_samples", ["dataset_version_id", "split"])
    op.create_index("idx_experiment_runs_created", "experiment_runs", [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_experiment_runs_created", table_name="experiment_runs")
    op.drop_table("experiment_runs")
    op.drop_constraint("fk_model_versions_dataset_version_id_dataset_versions", "model_versions", type_="foreignkey")
    op.drop_column("model_versions", "dataset_version_id")
    op.drop_index("idx_dataset_samples_version_split", table_name="dataset_samples")
    op.drop_table("dataset_samples")
    op.drop_index("idx_dataset_versions_dataset", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
