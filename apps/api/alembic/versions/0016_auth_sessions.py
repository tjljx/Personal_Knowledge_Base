"""Track server-side idle expiry for authenticated sessions."""

import sqlalchemy as sa

from alembic import op

revision = "0016_auth_sessions"
down_revision = "0015_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_activity", "auth_sessions", ["last_activity_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_activity", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_table("auth_sessions")
