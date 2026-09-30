"""Cloud model budget reservations and usage ledger."""

import sqlalchemy as sa

from alembic import op

revision = "0008_cloud_budget"
down_revision = "0007_chunk_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cloud_budget_months",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("spent_micro_cny", sa.BigInteger(), nullable=False),
        sa.Column("reserved_micro_cny", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "cloud_model_usage",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "month", sa.String(7), sa.ForeignKey("cloud_budget_months.month"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("reserved_micro_cny", sa.BigInteger(), nullable=False),
        sa.Column("charged_micro_cny", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cloud_usage_month_created", "cloud_model_usage", ["month", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_cloud_usage_month_created", table_name="cloud_model_usage")
    op.drop_table("cloud_model_usage")
    op.drop_table("cloud_budget_months")
