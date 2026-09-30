"""Permission-scoped hybrid retrieval for the current document versions."""

import logging
import unicodedata

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, aliased

from knowledge_api.config import get_settings
from knowledge_api.embeddings import EmbeddingError, cosine, embed_query
from knowledge_api.graph_evidence_policy import evidence_review_reason
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
)
from knowledge_api.reranking import RerankError, rerank_scores
from knowledge_api.vector_store import VectorStoreError, query_vectors

logger = logging.getLogger("knowledge_api.retrieval")


def _graph_candidates(db, kb_id, question, diagnostics=None):
    """Find source chunks through exact entity mentions and valid, current graph evidence."""
    normalized_question = unicodedata.normalize("NFKC", question).casefold()
    entities = db.execute(
        select(
            GraphEntity.id, GraphEntity.name, GraphEntity.normalized_name, GraphEntity.entity_type
        ).where(GraphEntity.knowledge_base_id == kb_id)
    ).all()
    matched = [
        entity
        for entity in entities
        if entity.normalized_name in normalized_question
        and (len(entity.normalized_name) >= 3 or entity.entity_type == "人物")
    ]
    matched.sort(key=lambda entity: len(entity.normalized_name), reverse=True)
    matched_ids = {entity.id for entity in matched[:8]}
    if not matched_ids:
        if diagnostics is not None:
            diagnostics["matched_entities"] = 0
            diagnostics["filtered_evidence"] = 0
        return [], {}
    if diagnostics is not None:
        diagnostics["matched_entities"] = len(matched_ids)
    source = aliased(GraphEntity)
    target = aliased(GraphEntity)
    evidence_rows = db.execute(
        select(GraphEvidence, source, target, Document, DocumentVersion, DocumentChunk)
        .join(source, source.id == GraphEvidence.source_entity_id)
        .join(target, target.id == GraphEvidence.target_entity_id)
        .join(DocumentVersion, DocumentVersion.id == GraphEvidence.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(DocumentChunk, DocumentChunk.id == GraphEvidence.chunk_id)
        .where(
            GraphEvidence.knowledge_base_id == kb_id,
            source.knowledge_base_id == kb_id,
            target.knowledge_base_id == kb_id,
            or_(source.id.in_(matched_ids), target.id.in_(matched_ids)),
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            Document.current_version == DocumentVersion.version_no,
            DocumentVersion.status == "PARSED",
            DocumentChunk.document_version_id == DocumentVersion.id,
        )
        .order_by(GraphEvidence.created_at.desc())
        .limit(500)
    ).all()
    candidates = {}
    clues = {}
    filtered_evidence = 0
    for evidence, source_entity, target_entity, document, version, chunk in evidence_rows:
        if evidence.quote not in chunk.content:
            continue
        if evidence_review_reason(
            evidence.quote,
            evidence.relation_type,
            source_entity.entity_type,
            target_entity.entity_type,
        ):
            filtered_evidence += 1
            continue
        both = source_entity.id in matched_ids and target_entity.id in matched_ids
        relation_match = evidence.relation_type.casefold() in normalized_question
        score = (4 if both else 0) + (2 if relation_match else 0)
        score += min(len(source_entity.name), 20) / 20 if source_entity.id in matched_ids else 0
        score += min(len(target_entity.name), 20) / 20 if target_entity.id in matched_ids else 0
        candidate = candidates.get(chunk.id)
        if candidate is None or score > candidate[0]:
            candidates[chunk.id] = (score, (document, version, chunk))
        clue = {
            "id": evidence.id,
            "source": source_entity.name,
            "relation": evidence.relation_type,
            "target": target_entity.name,
            "quote": evidence.quote,
        }
        clues.setdefault(chunk.id, []).append((score, clue))
    ranked = sorted(candidates.values(), key=lambda item: item[0], reverse=True)[:8]
    if diagnostics is not None:
        diagnostics["filtered_evidence"] = filtered_evidence
    evidence_by_chunk = {
        chunk_id: [item for _, item in sorted(items, key=lambda entry: entry[0], reverse=True)[:2]]
        for chunk_id, items in clues.items()
    }
    return [row for _, row in ranked], evidence_by_chunk


def _database_semantic(db, kb_id, query_vector, settings):
    """Preserve retrieval while Qdrant is unavailable or being populated."""
    embedded = db.execute(
        select(Document, DocumentVersion, DocumentChunk, DocumentChunkEmbedding)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_no == Document.current_version),
        )
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .join(DocumentChunkEmbedding, DocumentChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
            DocumentChunkEmbedding.model == settings.embedding_model,
        )
    ).all()
    semantic = [
        (score, row[:3])
        for row in embedded
        if (score := cosine(query_vector, row.DocumentChunkEmbedding.vector))
        >= settings.embedding_min_similarity
    ]
    semantic.sort(key=lambda item: item[0], reverse=True)
    return semantic


def _qdrant_semantic(db, kb_id, query_vector, settings):
    hits = query_vectors(settings, kb_id, query_vector)
    if not hits:
        return []
    ids = [chunk_id for chunk_id, _ in hits]
    rows = db.execute(
        select(Document, DocumentVersion, DocumentChunk)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_no == Document.current_version),
        )
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .where(
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
            DocumentChunk.id.in_(ids),
        )
    ).all()
    by_id = {row[2].id: row for row in rows}
    return [(score, by_id[chunk_id]) for chunk_id, score in hits if chunk_id in by_id]


def retrieve_chunks(
    db: Session,
    kb_id: str,
    question: str,
    current_terms: list[str],
    context_terms: list[str],
    limit: int = 8,
    include_graph: bool = True,
    trace: dict | None = None,
    diagnostics: dict | None = None,
) -> list:
    """Fuse lexical, local-vector, and source-grounded graph ranks."""
    base = (
        select(Document, DocumentVersion, DocumentChunk)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_no == Document.current_version),
        )
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .where(
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
        )
    )
    terms = current_terms + context_terms
    lexical = []
    if terms:
        conditions = [
            condition
            for term in terms
            for condition in (
                DocumentChunk.content.ilike(f"%{term}%"),
                Document.title.ilike(f"%{term}%"),
            )
        ]
        # SQL ordering prevents recent documents from crowding out older relevant ones.
        match_score = sum(
            (
                case((DocumentChunk.content.ilike(f"%{term}%"), weight), else_=0)
                + case((Document.title.ilike(f"%{term}%"), weight), else_=0)
                for term, weight in [
                    *((term, 3) for term in current_terms),
                    *((term, 1) for term in context_terms),
                ]
            )
        )
        lexical = db.execute(
            base.where(or_(*conditions))
            .order_by(match_score.desc(), Document.updated_at.desc(), DocumentChunk.id)
            .limit(500)
        ).all()
        lexical.sort(
            key=lambda row: (
                sum(
                    3 * min(row.DocumentChunk.content.casefold().count(term), 3)
                    + 2 * (term in row.Document.title.casefold())
                    for term in current_terms
                )
                + sum(
                    min(row.DocumentChunk.content.casefold().count(term), 3)
                    + (term in row.Document.title.casefold())
                    for term in context_terms
                )
            ),
            reverse=True,
        )

    semantic = []
    settings = get_settings()
    if settings.embedding_model:
        try:
            query_vector = embed_query(settings, question)
        except EmbeddingError:
            logger.warning("Semantic query unavailable; using lexical results")
        else:
            if settings.vector_store_url:
                try:
                    semantic = _qdrant_semantic(db, kb_id, query_vector, settings)
                except VectorStoreError:
                    logger.warning("Qdrant unavailable; using database vector projection")
                    semantic = _database_semantic(db, kb_id, query_vector, settings)
            else:
                semantic = _database_semantic(db, kb_id, query_vector, settings)

    graph_details = {} if diagnostics is not None else None
    graph, graph_clues = (
        _graph_candidates(db, kb_id, question, graph_details) if include_graph else ([], {})
    )
    fused: dict[str, tuple[float, object]] = {}
    channels: dict[str, set[str]] = {}
    for rank, row in enumerate(lexical, 1):
        fused[row[2].id] = (1 / (60 + rank), row)
        channels.setdefault(row[2].id, set()).add("keyword")
    for rank, (_, row) in enumerate(semantic, 1):
        chunk_id = row[2].id
        score, _ = fused.get(chunk_id, (0.0, row))
        fused[chunk_id] = (score + 1 / (60 + rank), row)
        channels.setdefault(chunk_id, set()).add("vector")
    for rank, row in enumerate(graph, 1):
        chunk_id = row[2].id
        score, _ = fused.get(chunk_id, (0.0, row))
        fused[chunk_id] = (score + 1 / (60 + rank), row)
        channels.setdefault(chunk_id, set()).add("graph")
    pool_size = max(limit, settings.rerank_candidate_limit)
    fused_ranked = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    ranked = fused_ranked[:pool_size]
    reranker_applied = False
    if settings.rerank_base_url and len(ranked) > 1:
        try:
            scores = rerank_scores(settings, question, [row[2].content for _, row in ranked])
            ranked = [
                ranked[index]
                for index in sorted(range(len(ranked)), key=lambda i: scores[i], reverse=True)
            ]
            reranker_applied = True
        except RerankError:
            logger.warning("Cross-encoder unavailable; using fused retrieval ranks")
    result = [row for _, row in ranked[:limit]]
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            {
                "keyword": [row[2].id for row in lexical],
                "vector": [row[1][2].id for row in semantic],
                "graph": [row[2].id for row in graph],
                "fused": [row[1][2].id for row in fused_ranked],
                "reranked": [row[1][2].id for row in ranked],
                "final": [row[2].id for row in result],
                "reranker_applied": reranker_applied,
                "graph_details": graph_details or {},
            }
        )
    if trace is not None:
        trace.clear()
        trace.update(
            {
                row[2].id: {
                    "channels": sorted(channels[row[2].id]),
                    "graph_evidence": graph_clues.get(row[2].id, [])
                    if "graph" in channels[row[2].id]
                    else [],
                }
                for row in result
            }
        )
    return result
