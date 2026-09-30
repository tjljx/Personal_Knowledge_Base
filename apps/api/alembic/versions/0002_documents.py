"""Immutable document versions and upload metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0002_documents"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_kb_created", "documents", ["knowledge_base_id", "created_at"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(as_uuid=False), sa.ForeignKey("documents.id"), nullable=False
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "version_no"),
    )


def downgrade() -> None:
    op.drop_table("document_versions")
    op.drop_index("ix_documents_kb_created", table_name="documents")
    op.drop_table("documents")
