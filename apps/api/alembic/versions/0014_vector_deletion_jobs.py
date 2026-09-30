"""Keep Qdrant deletion requests after reparsed chunks are replaced."""

import sqlalchemy as sa

from alembic import op

revision = "0014_vector_deletion_jobs"
down_revision = "0013_vector_projection_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vector_deletion_jobs",
        sa.Column("chunk_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vector_deletion_jobs")
