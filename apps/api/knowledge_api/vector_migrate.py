"""Idempotently copy current PostgreSQL vectors to the dedicated Qdrant index."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.db import get_engine
from knowledge_api.models import Document, DocumentChunk, DocumentChunkEmbedding, DocumentVersion
from knowledge_api.vector_store import upsert_vectors


def main() -> None:
    settings = get_settings()
    if not settings.vector_store_url or not settings.embedding_model:
        raise SystemExit("Vector store and embedding model must be configured")
    with Session(get_engine()) as db:
        rows = db.execute(
            select(DocumentChunk, Document, DocumentVersion, DocumentChunkEmbedding)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(DocumentChunkEmbedding, DocumentChunkEmbedding.chunk_id == DocumentChunk.id)
            .where(
                Document.current_version == DocumentVersion.version_no,
                Document.status != "DELETED",
                DocumentVersion.status == "PARSED",
                DocumentChunkEmbedding.model == settings.embedding_model,
            )
            .order_by(DocumentChunk.id)
        ).all()
        count = 0
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            upsert_vectors(
                settings,
                [
                    {
                        "id": chunk.id,
                        "vector": embedding.vector,
                        "payload": {
                            "knowledge_base_id": document.knowledge_base_id,
                            "document_id": document.id,
                            "document_version_id": version.id,
                            "model": settings.embedding_model,
                        },
                    }
                    for chunk, document, version, embedding in batch
                ],
            )
            count += len(batch)
    print(f"migrated_current_vectors={count}")


if __name__ == "__main__":
    main()
