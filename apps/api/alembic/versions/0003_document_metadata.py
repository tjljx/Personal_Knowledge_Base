"""Document metadata, tags, and update time."""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0003_document_metadata"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("documents")}
    if "description" not in columns:
        op.add_column(
            "documents", sa.Column("description", sa.Text(), nullable=False, server_default="")
        )
    if "source" not in columns:
        op.add_column(
            "documents", sa.Column("source", sa.String(200), nullable=False, server_default="")
        )
    if "file_type" not in columns:
        op.add_column(
            "documents", sa.Column("file_type", sa.String(20), nullable=False, server_default="")
        )
    if "updated_at" not in columns:
        op.add_column(
            "documents",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default="1970-01-01 00:00:00",
            ),
        )
    connection.execute(sa.text("UPDATE documents SET updated_at = created_at"))
    if not sa.inspect(connection).has_table("document_tags"):
        op.create_table(
            "document_tags",
            sa.Column(
                "document_id",
                sa.Uuid(as_uuid=False),
                sa.ForeignKey("documents.id"),
                primary_key=True,
            ),
            sa.Column("name", sa.String(50), primary_key=True),
        )
    existing = connection.execute(
        sa.text(
            "SELECT d.id, v.original_filename FROM documents d "
            "JOIN document_versions v ON v.document_id = d.id AND v.version_no = d.current_version"
        )
    )
    for document_id, filename in existing:
        connection.execute(
            sa.text("UPDATE documents SET file_type = :file_type WHERE id = :id"),
            {"file_type": Path(filename).suffix.lower().lstrip("."), "id": document_id},
        )


def downgrade() -> None:
    op.drop_table("document_tags")
    op.drop_column("documents", "updated_at")
    op.drop_column("documents", "file_type")
    op.drop_column("documents", "source")
    op.drop_column("documents", "description")
