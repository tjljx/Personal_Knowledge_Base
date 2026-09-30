"""Requeue older graph extraction results after provider mode correction."""

import sqlalchemy as sa

from alembic import op

revision = "0012_graph_extractor_version"
down_revision = "0011_knowledge_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_chunk_extractions",
        sa.Column("extractor_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("graph_chunk_extractions", "extractor_version")
