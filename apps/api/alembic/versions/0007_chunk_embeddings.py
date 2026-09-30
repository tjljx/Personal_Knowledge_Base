"""Store rebuildable local embeddings for version-bound chunks."""

import sqlalchemy as sa

from alembic import op

revision = "0007_chunk_embeddings"
down_revision = "0006_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_chunk_embeddings",
        sa.Column(
            "chunk_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chunk_embeddings_model", "document_chunk_embeddings", ["model"])


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_model", table_name="document_chunk_embeddings")
    op.drop_table("document_chunk_embeddings")
