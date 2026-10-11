"""Create the PostgreSQL enterprise schema.

Revision ID: 20261006_0001
Revises:
"""
from __future__ import annotations

from alembic import op

from app.models import metadata

revision = "20261006_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), checkfirst=True)
