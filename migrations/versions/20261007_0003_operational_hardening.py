"""Add release lifecycle and reliable outbox scheduling.

Revision ID: 20261007_0003
Revises: 20261006_0002
"""
import sqlalchemy as sa
from alembic import op

revision = "20261007_0003"
down_revision = "20261006_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "release_stage" in {
        column["name"] for column in inspector.get_columns("model_versions")
    }:
        return
    op.add_column("model_versions", sa.Column("release_stage", sa.String(24), nullable=False, server_default="candidate"))
    op.add_column("model_versions", sa.Column("artifact_signature", sa.String(128), nullable=False, server_default=""))
    op.add_column("model_versions", sa.Column("supersedes_model_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_model_versions_supersedes_model_id_model_versions", "model_versions", "model_versions", ["supersedes_model_id"], ["id"], ondelete="SET NULL")
    op.add_column("integration_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integration_events", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integration_events", sa.Column("locked_by", sa.String(120), nullable=False, server_default=""))
    op.add_column("integration_events", sa.Column("last_error", sa.Text(), nullable=False, server_default=""))
    op.add_column("integration_events", sa.Column("idempotency_key", sa.String(160), nullable=True))
    op.execute("UPDATE integration_events SET idempotency_key = 'event-' || id")
    op.alter_column("integration_events", "idempotency_key", nullable=False)
    op.create_unique_constraint("uq_integration_events_idempotency_key", "integration_events", ["idempotency_key"])
    op.create_index("idx_integration_events_due", "integration_events", ["status", "next_attempt_at", "id"])
    op.execute("UPDATE model_versions SET release_stage = CASE WHEN is_active THEN 'active' WHEN gate_status = 'passed' THEN 'approved' WHEN gate_status = 'blocked' THEN 'rejected' ELSE 'candidate' END")


def downgrade() -> None:
    op.drop_index("idx_integration_events_due", table_name="integration_events")
    op.drop_constraint("uq_integration_events_idempotency_key", "integration_events", type_="unique")
    for name in ("idempotency_key", "last_error", "locked_by", "locked_at", "next_attempt_at"):
        op.drop_column("integration_events", name)
    op.drop_constraint("fk_model_versions_supersedes_model_id_model_versions", "model_versions", type_="foreignkey")
    for name in ("supersedes_model_id", "artifact_signature", "release_stage"):
        op.drop_column("model_versions", name)
