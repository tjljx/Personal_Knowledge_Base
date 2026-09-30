"""Persistent knowledge-base chat sessions."""

import sqlalchemy as sa

from alembic import op

revision = "0006_chat_sessions"
down_revision = "0005_document_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_chat_sessions_kb_user_updated",
        "chat_sessions",
        ["knowledge_base_id", "user_id", "updated_at"],
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(as_uuid=False), sa.ForeignKey("chat_sessions.id"), nullable=False
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_kb_user_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
