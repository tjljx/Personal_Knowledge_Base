"""Version-bound retrieval chunks."""

import sqlalchemy as sa

from alembic import op

revision = "0005_document_chunks"
down_revision = "0004_document_parsing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer()),
        sa.Column("block_type", sa.String(40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_version_id", "ordinal"),
    )
    op.create_index(
        "ix_document_chunks_version_page",
        "document_chunks",
        ["document_version_id", "page_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_version_page", table_name="document_chunks")
    op.drop_table("document_chunks")
