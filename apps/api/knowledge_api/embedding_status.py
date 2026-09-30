"""Operational status and live probe for the local embedding index."""

import argparse
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.db import get_engine
from knowledge_api.embeddings import EmbeddingError, embed_query
from knowledge_api.models import Document, DocumentChunk, DocumentChunkEmbedding, DocumentVersion
from knowledge_api.vector_store import VectorStoreError, present_ids


def index_status(db: Session) -> dict:
    settings = get_settings()
    current = (
        select(DocumentChunk.id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.current_version == DocumentVersion.version_no,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
        )
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(current)) or 0
    indexed = 0
    if settings.embedding_model:
        indexed = (
            db.scalar(
                select(func.count())
                .select_from(current)
                .join(DocumentChunkEmbedding, DocumentChunkEmbedding.chunk_id == current.c.id)
                .where(DocumentChunkEmbedding.model == settings.embedding_model)
            )
            or 0
        )
    status = {
        "model": settings.embedding_model,
        "expected_dimensions": settings.embedding_expected_dimensions,
        "current_chunks": total,
        "indexed_current_chunks": indexed,
        "pending_current_chunks": total - indexed,
    }
    if settings.vector_store_url:
        ids = list(db.scalars(select(current.c.id)))
        try:
            status["qdrant_indexed_current_chunks"] = len(present_ids(settings, ids))
            status["qdrant_pending_current_chunks"] = (
                total - status["qdrant_indexed_current_chunks"]
            )
        except VectorStoreError as exc:
            status["qdrant_error"] = str(exc)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Check embedding model and index coverage")
    parser.add_argument(
        "--probe", action="store_true", help="Embed a short query with the live model"
    )
    args = parser.parse_args()
    with Session(get_engine()) as db:
        status = index_status(db)
    if args.probe:
        try:
            status["probe_dimensions"] = len(embed_query(get_settings(), "知识库检索测试"))
            status["probe_ok"] = True
        except EmbeddingError as exc:
            status["probe_ok"] = False
            status["probe_error"] = str(exc)
    print(json.dumps(status, ensure_ascii=False))
    if (
        status["model"] is None
        or status["pending_current_chunks"]
        or status.get("qdrant_pending_current_chunks")
        or status.get("qdrant_error")
        or status.get("probe_ok") is False
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
