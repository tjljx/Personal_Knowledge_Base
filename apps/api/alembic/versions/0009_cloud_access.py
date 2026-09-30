"""Default-deny cloud access for each knowledge base."""

import sqlalchemy as sa

from alembic import op

revision = "0009_cloud_access"
down_revision = "0008_cloud_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("cloud_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "cloud_enabled")
