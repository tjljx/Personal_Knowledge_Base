"""Create source-anchored question drafts for human review.

The questions and labels are derived from graph evidence. They are useful for
review and regression checks, but must not be reported as a human gold set.
"""

import argparse
import json
from collections import defaultdict
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


def generate(db: Session, kb_id: str, limit: int = 100) -> list[dict]:
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
            Document.knowledge_base_id == kb_id,
            Document.status != "DELETED",
            Document.current_version == DocumentVersion.version_no,
            DocumentVersion.status == "PARSED",
            DocumentChunk.document_version_id == DocumentVersion.id,
        )
        .order_by(Document.title, GraphEvidence.id)
    ).all()
    by_document: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for evidence, origin, destination, document, version, chunk in rows:
        quote = evidence.quote.strip()
        if not quote or quote not in chunk.content:
            continue
        if evidence_review_reason(
            quote, evidence.relation_type, origin.entity_type, destination.entity_type
        ):
            continue
        if len(origin.normalized_name) < 3 or len(destination.normalized_name) < 3:
            continue
        key = (origin.normalized_name, destination.normalized_name, evidence.relation_type)
        if key in seen:
            continue
        seen.add(key)
        by_document[document.id].append(
            {
                "question": f"{origin.name}与{destination.name}是什么关系？",
                "expected_document_ids": [document.id],
                "expected_chunk_ids": [chunk.id],
                "expected_graph_evidence_ids": [evidence.id],
                "expected_answer_points": [evidence.relation_type],
                "source_document_title": document.title,
                "source_version": version.version_no,
                "source_page": chunk.page_no,
                "source_quote": quote,
                "should_refuse": False,
                "tags": ["relation", "auto_generated", "needs_human_review"],
                "review_status": "pending",
            }
        )
    result = []
    document_ids = sorted(
        by_document, key=lambda doc_id: by_document[doc_id][0]["source_document_title"]
    )
    while len(result) < limit:
        added = False
        for document_id in document_ids:
            if by_document[document_id]:
                result.append(by_document[document_id].pop(0))
                added = True
                if len(result) == limit:
                    break
        if not added:
            break
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export source-anchored evaluation question drafts"
    )
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    with Session(get_engine()) as db:
        cases = generate(db, args.kb_id, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + ("\n" if cases else ""),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "kb_id": args.kb_id,
                "drafts": len(cases),
                "documents": len({case["expected_document_ids"][0] for case in cases}),
                "output": str(args.output),
                "review_status": "pending",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
