"""Read-only audit of graph evidence excluded from answer retrieval."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from knowledge_api.db import get_engine
from knowledge_api.graph_evidence_policy import evidence_review_reason
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
)


def audit(db: Session, kb_id: str) -> dict:
    source = aliased(GraphEntity)
    target = aliased(GraphEntity)
    rows = db.execute(
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
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            Document.current_version == DocumentVersion.version_no,
            DocumentVersion.status == "PARSED",
            DocumentChunk.document_version_id == DocumentVersion.id,
        )
    ).all()
    flagged = []
    for evidence, origin, destination, document, version, chunk in rows:
        reason = (
            "quote_not_in_current_chunk"
            if evidence.quote not in chunk.content
            else evidence_review_reason(
                evidence.quote,
                evidence.relation_type,
                origin.entity_type,
                destination.entity_type,
            )
        )
        if not reason:
            continue
        flagged.append(
            {
                "evidence_id": evidence.id,
                "reason": reason,
                "source": origin.name,
                "relation": evidence.relation_type,
                "target": destination.name,
                "quote": evidence.quote,
                "document_id": document.id,
                "document_title": document.title,
                "version_no": version.version_no,
                "page_no": chunk.page_no,
                "chunk_id": chunk.id,
            }
        )
    return {
        "kb_id": kb_id,
        "current_evidence": len(rows),
        "flagged_count": len(flagged),
        "flagged": flagged,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit graph evidence excluded from retrieval")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with Session(get_engine()) as db:
        report = audit(db, args.kb_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "kb_id": args.kb_id,
                "current_evidence": report["current_evidence"],
                "flagged_count": report["flagged_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
