"""Version-bound parsing jobs and results."""

import sqlalchemy as sa

from alembic import op

revision = "0004_document_parsing"
down_revision = "0003_document_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("parse_error", sa.String(500)))
    op.create_table(
        "document_parse_jobs",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "document_parse_results",
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_versions.id"),
            primary_key=True,
        ),
        sa.Column("parser_name", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(30), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("structure", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("document_parse_results")
    op.drop_table("document_parse_jobs")
    op.drop_column("document_versions", "parse_error")
