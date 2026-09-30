"""Keep Qdrant aligned with active document versions without losing history."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentVersion,
    VectorDeletionJob,
    VectorProjectionJob,
)
from knowledge_api.vector_store import VectorStoreError, delete_vectors, upsert_vectors

logger = logging.getLogger("knowledge_api.document_lifecycle")


def queue_document_projection(db: Session, document_id: str) -> None:
    """Revisit every version so inactive vectors are removed and active ones restored."""
    ids = db.scalars(
        select(DocumentChunk.id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .where(DocumentVersion.document_id == document_id)
    ).all()
    existing = (
        set(
            db.scalars(
                select(VectorProjectionJob.chunk_id).where(VectorProjectionJob.chunk_id.in_(ids))
            ).all()
        )
        if ids
        else set()
    )
    db.add_all(
        VectorProjectionJob(chunk_id=chunk_id) for chunk_id in ids if chunk_id not in existing
    )


def queue_chunk_deletions(db: Session, ids: list[str]) -> None:
    if not ids:
        return
    existing = set(
        db.scalars(
            select(VectorDeletionJob.chunk_id).where(VectorDeletionJob.chunk_id.in_(ids))
        ).all()
    )
    db.add_all(VectorDeletionJob(chunk_id=chunk_id) for chunk_id in ids if chunk_id not in existing)


def reconcile_next_projection(db: Session) -> bool:
    settings = get_settings()
    if not settings.vector_store_url:
        return False
    obsolete = db.scalars(
        select(VectorDeletionJob).order_by(VectorDeletionJob.created_at).limit(100)
    ).all()
    if obsolete:
        try:
            delete_vectors(settings, [row.chunk_id for row in obsolete])
        except VectorStoreError:
            logger.warning("Qdrant obsolete point deletion failed; retrying later")
            db.rollback()
            return False
        for row in obsolete:
            db.delete(row)
        db.commit()
        return True
    jobs = db.scalars(
        select(VectorProjectionJob).order_by(VectorProjectionJob.created_at).limit(50)
    ).all()
    if not jobs:
        return False
    upserts = []
    removals = []
    for job in jobs:
        chunk = db.get(DocumentChunk, job.chunk_id)
        if chunk is None:
            db.delete(job)
            continue
        version = db.get(DocumentVersion, chunk.document_version_id)
        document = db.get(Document, version.document_id)
        embedding = db.get(DocumentChunkEmbedding, chunk.id)
        if (
            document.status != "DELETED"
            and document.current_version == version.version_no
            and version.status == "PARSED"
            and embedding is not None
            and embedding.model == settings.embedding_model
        ):
            upserts.append(
                {
                    "id": chunk.id,
                    "vector": embedding.vector,
                    "payload": {
                        "knowledge_base_id": document.knowledge_base_id,
                        "document_id": document.id,
                        "document_version_id": version.id,
                        "model": embedding.model,
                    },
                }
            )
        else:
            removals.append(chunk.id)
    try:
        upsert_vectors(settings, upserts)
        delete_vectors(settings, removals)
    except VectorStoreError:
        logger.warning("Qdrant projection reconciliation failed; retrying later")
        db.rollback()
        return False
    for job in jobs:
        db.delete(job)
    db.commit()
    return True
