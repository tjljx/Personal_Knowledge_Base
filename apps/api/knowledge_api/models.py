from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from knowledge_api.db import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="MEMBER")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    cloud_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeBaseMember(Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "user_id"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    member_role: Mapped[str] = mapped_column(String(20), nullable=False, default="EDITOR")


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_activity", "last_activity_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempt_identity_time", "username", "ip_address", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_kb_created", "knowledge_base_id", "created_at"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UPLOADED")
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    tag_entries: Mapped[list["DocumentTag"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def tags(self) -> list[str]:
        return sorted(entry.name for entry in self.tag_entries)


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50), primary_key=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UPLOADED")
    parse_error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    parse_job: Mapped["DocumentParseJob | None"] = relationship(lazy="selectin", uselist=False)

    @property
    def job_status(self) -> str | None:
        return self.parse_job.status if self.parse_job else None


class DocumentParseJob(Base):
    __tablename__ = "document_parse_jobs"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_versions.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentParseResult(Base):
    __tablename__ = "document_parse_results"

    document_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_versions.id"), primary_key=True
    )
    parser_name: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(30), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    structure: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal"),
        Index("ix_document_chunks_version_page", "document_version_id", "page_no"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_versions.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentChunkEmbedding(Base):
    __tablename__ = "document_chunk_embeddings"
    __table_args__ = (Index("ix_chunk_embeddings_model", "model"),)

    chunk_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VectorProjectionJob(Base):
    """Durable request to reconcile a chunk with the current Qdrant projection."""

    __tablename__ = "vector_projection_jobs"

    chunk_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VectorDeletionJob(Base):
    """Retain obsolete Qdrant point IDs after their PostgreSQL chunks are replaced."""

    __tablename__ = "vector_deletion_jobs"

    chunk_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "normalized_name", "entity_type"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)


class GraphEvidence(Base):
    __tablename__ = "graph_evidence"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relation_type", "chunk_id"),
        Index("ix_graph_evidence_kb", "knowledge_base_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("graph_entities.id"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("graph_entities.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    document_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_versions.id"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    quote: Mapped[str] = mapped_column(String(500), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GraphChunkExtraction(Base):
    __tablename__ = "graph_chunk_extractions"

    chunk_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    extractor_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_kb_user_updated", "knowledge_base_id", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("chat_sessions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudBudgetMonth(Base):
    __tablename__ = "cloud_budget_months"

    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    spent_micro_cny: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_micro_cny: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class CloudModelUsage(Base):
    __tablename__ = "cloud_model_usage"
    __table_args__ = (Index("ix_cloud_usage_month_created", "month", "created_at"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    month: Mapped[str] = mapped_column(
        String(7), ForeignKey("cloud_budget_months.month"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("knowledge_bases.id"), nullable=False
    )
    task: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    reserved_micro_cny: Mapped[int] = mapped_column(BigInteger, nullable=False)
    charged_micro_cny: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkerHeartbeat(Base):
    """Liveness marker written by every worker cycle, read by /health."""

    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str] = mapped_column(String(200), nullable=False, default="")
