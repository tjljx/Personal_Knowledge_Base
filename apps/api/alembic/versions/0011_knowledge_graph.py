"""Evidence backed entity relation graph."""

import sqlalchemy as sa

from alembic import op

revision = "0011_knowledge_graph"
down_revision = "0010_password_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_entities",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.UniqueConstraint("knowledge_base_id", "normalized_name", "entity_type"),
    )
    op.create_table(
        "graph_chunk_extractions",
        sa.Column(
            "chunk_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "graph_evidence",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column(
            "source_entity_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("graph_entities.id"),
            nullable=False,
        ),
        sa.Column(
            "target_entity_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("graph_entities.id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quote", sa.String(500), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_entity_id", "target_entity_id", "relation_type", "chunk_id"),
    )
    op.create_index("ix_graph_evidence_kb", "graph_evidence", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_evidence_kb", table_name="graph_evidence")
    op.drop_table("graph_evidence")
    op.drop_table("graph_chunk_extractions")
    op.drop_table("graph_entities")
