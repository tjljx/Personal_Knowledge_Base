"""Track durable Qdrant updates after document lifecycle changes."""

import sqlalchemy as sa

from alembic import op

revision = "0013_vector_projection_jobs"
down_revision = "0012_graph_extractor_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vector_projection_jobs",
        sa.Column(
            "chunk_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vector_projection_jobs")
