"""Add model release gates and drift reports.

Revision ID: 20261006_0002
Revises: 20261006_0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261006_0002"
down_revision = "20261006_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0001 historically used the live SQLAlchemy metadata. A brand-new
    # installation may therefore already contain later schema objects, while an
    # existing 0001 database still needs this delta. Keep both upgrade paths valid.
    inspector = sa.inspect(op.get_bind())
    if "gate_status" in {column["name"] for column in inspector.get_columns("model_versions")}:
        return
    op.add_column("model_versions", sa.Column("gate_status", sa.String(24), nullable=False, server_default="pending"))
    op.add_column("model_versions", sa.Column("gate_report_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("model_versions", sa.Column("published_by", sa.String(120), nullable=True))
    op.add_column("model_versions", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "drift_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.BigInteger(), sa.ForeignKey("model_versions.id", ondelete="SET NULL")),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("psi", sa.Float(), nullable=False),
        sa.Column("mean_shift", sa.Float(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_drift_product_created", "drift_reports", ["product_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("drift_reports")
    for name in ("published_at", "published_by", "gate_report_json", "gate_status"):
        op.drop_column("model_versions", name)
