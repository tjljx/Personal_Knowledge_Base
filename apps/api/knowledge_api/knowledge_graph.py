"""Extract and serve source grounded entity relations."""

import json
import logging
import re
import unicodedata
from datetime import timedelta

import httpx
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from knowledge_api.cloud_budget import (
    BudgetExceeded,
    BudgetNotConfigured,
    reserve_call,
    settle_call,
)
from knowledge_api.config import get_settings
from knowledge_api.graph_evidence_policy import evidence_review_reason
from knowledge_api.model_gateway import ModelAnswer
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentParseResult,
    DocumentVersion,
    GraphChunkExtraction,
    GraphEntity,
    GraphEvidence,
    KnowledgeBase,
    utc_now,
)

logger = logging.getLogger(__name__)
EXTRACTOR_VERSION = 3
MAX_GRAPH_OUTPUT_TOKENS = 2000
ENTITY_TYPES = {"人物", "组织", "地点", "产品", "技术", "概念", "事件", "其他"}


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def parse_relations(content: str, chunk_text: str) -> list[dict]:
    """Reject unsupported claims, even when the model returns valid JSON."""
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    data = json.loads(clean)
    rows = data.get("relations", [])
    if not isinstance(rows, list):
        raise TypeError("relations must be a list")
    valid = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source", "")).strip()[:160]
        target = str(row.get("target", "")).strip()[:160]
        relation = str(row.get("relation", "")).strip()[:80]
        quote = str(row.get("quote", "")).strip()[:500]
        source_type = str(row.get("source_type", "其他")).strip()
        target_type = str(row.get("target_type", "其他")).strip()
        if (
            len(source) < 2
            or len(target) < 2
            or not relation
            or len(quote) < 8
            or source == target
            or quote not in chunk_text
            or normalize_name(source) not in normalize_name(quote)
            or normalize_name(target) not in normalize_name(quote)
        ):
            continue
        if evidence_review_reason(quote, relation, source_type, target_type):
            continue
        valid.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "quote": quote,
                "source_type": source_type if source_type in ENTITY_TYPES else "其他",
                "target_type": target_type if target_type in ENTITY_TYPES else "其他",
            }
        )
    return valid


def _extract(settings, text: str, context: str = "") -> ModelAnswer:
    prompt = (
        "从资料中抽取明确写出的实体关系。只输出 JSON 对象，格式为 "
        '{"relations":[{"source":"实体A","source_type":"组织","relation":"关系",'
        '"target":"实体B","target_type":"产品","quote":"包含两个实体和关系的原文连续摘录"}]}。'
        "实体类型限人物、组织、地点、产品、技术、概念、事件、其他。"
        "不要推断；quote 必须与原文逐字一致；无明确关系则返回空数组。\n\n资料：\n" + text
    )
    prompt += "\n\n最多返回 8 条有逐字证据的关键关系，确保 JSON 完整。"
    if context:
        prompt += (
            "\n\n文档概要仅供理解主题，不能作为证据；quote 必须逐字来自上面的资料：\n" + context
        )
    try:
        response = httpx.post(
            f"{settings.model_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}"},
            json={
                "model": settings.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": min(settings.model_max_output_tokens, MAX_GRAPH_OUTPUT_TOKENS),
                **(
                    {"thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}}
                    if settings.model_provider == "deepseek"
                    else {}
                ),
            },
            timeout=settings.model_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        return ModelAnswer(
            payload["choices"][0]["message"]["content"],
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("实体关系模型调用失败") from exc


def _entity(db: Session, kb_id: str, name: str, entity_type: str) -> GraphEntity:
    normalized = normalize_name(name)
    entity = db.scalar(
        select(GraphEntity).where(
            GraphEntity.knowledge_base_id == kb_id,
            GraphEntity.normalized_name == normalized,
            GraphEntity.entity_type == entity_type,
        )
    )
    if entity is None:
        entity = GraphEntity(
            knowledge_base_id=kb_id, name=name, normalized_name=normalized, entity_type=entity_type
        )
        db.add(entity)
        db.flush()
    return entity


def backfill_next_graph(db: Session) -> bool:
    settings = get_settings()
    if settings.model_api_key is None:
        return False
    row = db.execute(
        select(DocumentChunk, DocumentVersion, Document, KnowledgeBase)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .outerjoin(GraphChunkExtraction, GraphChunkExtraction.chunk_id == DocumentChunk.id)
        .where(
            or_(
                GraphChunkExtraction.chunk_id.is_(None),
                (GraphChunkExtraction.extractor_version < 2)
                & (GraphChunkExtraction.status.notin_(["RUNNING", "WAITING_BUDGET"])),
                (GraphChunkExtraction.status == "FAILED")
                & (GraphChunkExtraction.extractor_version < EXTRACTOR_VERSION),
                (GraphChunkExtraction.status == "RUNNING")
                & (GraphChunkExtraction.updated_at < utc_now() - timedelta(minutes=15)),
                (GraphChunkExtraction.status == "WAITING_BUDGET")
                & (GraphChunkExtraction.updated_at < utc_now() - timedelta(hours=1)),
            ),
            KnowledgeBase.cloud_enabled.is_(True),
            Document.current_version == DocumentVersion.version_no,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
        )
        .order_by(DocumentChunk.created_at, DocumentChunk.id)
        .limit(1)
    ).first()
    if row is None:
        return False
    chunk, version, _document, kb = row
    marker = db.get(GraphChunkExtraction, chunk.id)
    if marker is None:
        marker = GraphChunkExtraction(
            chunk_id=chunk.id,
            status="RUNNING",
            extractor_version=EXTRACTOR_VERSION,
            updated_at=utc_now(),
        )
        db.add(marker)
    else:
        marker.status = "RUNNING"
        marker.updated_at = utc_now()
    db.commit()
    if len(chunk.content.strip()) < 30:
        marker.status = "SUCCEEDED"
        marker.extractor_version = EXTRACTOR_VERSION
        db.commit()
        return True
    content = chunk.content[:2000]
    parsed = db.get(DocumentParseResult, version.id)
    summary = str(parsed.structure.get("semantic_summary", ""))[:500] if parsed else ""
    extraction_input = (summary + "\n" + content) if summary else content
    try:
        usage = reserve_call(
            db,
            settings,
            kb.owner_id,
            kb.id,
            extraction_input,
            [],
            None,
            task="graph_extraction",
            max_output_tokens=min(settings.model_max_output_tokens, MAX_GRAPH_OUTPUT_TOKENS),
        )
    except (BudgetNotConfigured, BudgetExceeded) as exc:
        marker.status = "WAITING_BUDGET"
        marker.error = str(exc)[:500]
        db.commit()
        return False
    answer = None
    try:
        answer = _extract(settings, content, summary) if summary else _extract(settings, content)
        relations = parse_relations(answer.content, content)
    except (ValueError, TypeError) as exc:
        marker.status = "FAILED"
        marker.error = str(exc)[:500]
        logger.warning("Graph extraction failed for chunk %s: %s", chunk.id, exc)
        relations = []
    finally:
        settle_call(db, settings, usage, answer)
    if marker.status != "FAILED":
        db.execute(delete(GraphEvidence).where(GraphEvidence.chunk_id == chunk.id))
        unique_relations = set()
        for relation in relations:
            source = _entity(db, kb.id, relation["source"], relation["source_type"])
            target = _entity(db, kb.id, relation["target"], relation["target_type"])
            relation_key = (source.id, target.id, relation["relation"])
            if source.id != target.id and relation_key not in unique_relations:
                unique_relations.add(relation_key)
                db.add(
                    GraphEvidence(
                        knowledge_base_id=kb.id,
                        source_entity_id=source.id,
                        target_entity_id=target.id,
                        relation_type=relation["relation"],
                        document_version_id=version.id,
                        chunk_id=chunk.id,
                        quote=relation["quote"],
                        confidence=0.75,
                        model=settings.model_name,
                    )
                )
        marker.status = "SUCCEEDED"
    marker.extractor_version = EXTRACTOR_VERSION
    marker.updated_at = utc_now()
    db.commit()
    return True


def graph_output(db: Session, kb_id: str) -> dict:
    source_entity = aliased(GraphEntity)
    target_entity = aliased(GraphEntity)
    rows = db.execute(
        select(
            GraphEvidence, source_entity, target_entity, Document, DocumentVersion, DocumentChunk
        )
        .join(source_entity, source_entity.id == GraphEvidence.source_entity_id)
        .join(target_entity, target_entity.id == GraphEvidence.target_entity_id)
        .join(DocumentVersion, DocumentVersion.id == GraphEvidence.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(DocumentChunk, DocumentChunk.id == GraphEvidence.chunk_id)
        .where(
            GraphEvidence.knowledge_base_id == kb_id,
            Document.current_version == DocumentVersion.version_no,
            DocumentVersion.status == "PARSED",
            Document.status != "DELETED",
        )
        .order_by(GraphEvidence.created_at.desc())
    ).all()
    nodes = {}
    edges = {}
    for evidence, source, target, document, version, chunk in rows:
        if evidence.quote not in chunk.content:
            continue
        if evidence_review_reason(
            evidence.quote, evidence.relation_type, source.entity_type, target.entity_type
        ):
            continue
        for entity in (source, target):
            nodes[entity.id] = {"id": entity.id, "name": entity.name, "type": entity.entity_type}
        key = (source.id, target.id, evidence.relation_type)
        edge = edges.setdefault(
            key,
            {
                "id": "|".join(key),
                "source": source.id,
                "target": target.id,
                "relation": evidence.relation_type,
                "evidence": [],
            },
        )
        edge["evidence"].append(
            {
                "document_id": document.id,
                "document_title": document.title,
                "version_no": version.version_no,
                "page_no": chunk.page_no,
                "chunk_id": chunk.id,
                "quote": evidence.quote,
                "model": evidence.model,
            }
        )
    progress = graph_progress(db, kb_id)
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "processed_chunks": progress["processed_chunks"],
        "failed_chunks": progress["failed_chunks"],
    }


def graph_progress(db: Session, kb_id: str) -> dict:
    """Count current-version chunks without loading graph entities or evidence."""
    rows = db.execute(
        select(GraphChunkExtraction.status, func.count(DocumentChunk.id))
        .select_from(DocumentChunk)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .outerjoin(GraphChunkExtraction, GraphChunkExtraction.chunk_id == DocumentChunk.id)
        .where(
            Document.knowledge_base_id == kb_id,
            Document.current_version == DocumentVersion.version_no,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
        )
        .group_by(GraphChunkExtraction.status)
    ).all()
    counts = {status or "PENDING": count for status, count in rows}
    total = sum(counts.values())
    processed = counts.get("SUCCEEDED", 0)
    documents = db.execute(
        select(Document.status, func.count(Document.id))
        .where(Document.knowledge_base_id == kb_id, Document.status != "DELETED")
        .group_by(Document.status)
    ).all()
    document_counts = dict(documents)
    return {
        "total_chunks": total,
        "processed_chunks": processed,
        "remaining_chunks": total - processed,
        "running_chunks": counts.get("RUNNING", 0),
        "failed_chunks": counts.get("FAILED", 0),
        "waiting_budget_chunks": counts.get("WAITING_BUDGET", 0),
        "total_documents": sum(document_counts.values()),
        "parsed_documents": document_counts.get("PARSED", 0),
    }
