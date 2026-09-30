"""Initial identity, knowledge base, auth, and audit schema.

Revision ID: 0001_initial
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "knowledge_base_members",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("member_role", sa.String(20), nullable=False),
        sa.UniqueConstraint("knowledge_base_id", "user_id"),
    )
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_login_attempt_identity_time", "login_attempts", ["username", "ip_address", "created_at"]
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=False)),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_index("ix_login_attempt_identity_time", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_table("revoked_tokens")
    op.drop_table("knowledge_base_members")
    op.drop_table("knowledge_bases")
    op.drop_table("users")
