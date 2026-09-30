"""Recreate the worker heartbeat table used by /health and the worker healthcheck."""

import sqlalchemy as sa

from alembic import op

revision = "0015_worker_heartbeats"
down_revision = "0014_vector_deletion_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.String(length=40), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
