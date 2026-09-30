"""Read-only, automatically labeled graph retrieval smoke comparison.

The labels are derived from existing graph evidence, so this is not a human
quality benchmark. Run inside the API container with --limit 12 or similar.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from knowledge_api.db import get_engine
from knowledge_api.evaluate_retrieval import evaluate
from knowledge_api.graph_evidence_policy import evidence_review_reason
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare graph retrieval on real current-version evidence"
    )
    parser.add_argument("--kb-id")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    source = aliased(GraphEntity)
    target = aliased(GraphEntity)
    with Session(get_engine()) as db:
        kb_id = args.kb_id or db.scalar(
            select(GraphEvidence.knowledge_base_id)
            .group_by(GraphEvidence.knowledge_base_id)
            .order_by(func.count(GraphEvidence.id).desc())
            .limit(1)
        )
        if not kb_id:
            raise SystemExit("No graph evidence available")
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
                Document.current_version == DocumentVersion.version_no,
                DocumentVersion.status == "PARSED",
                Document.status != "DELETED",
                DocumentChunk.document_version_id == DocumentVersion.id,
            )
            .order_by(GraphEvidence.created_at.desc())
            .limit(2500)
        ).all()
        cases = []
        used_documents = set()
        for evidence, origin, destination, document, _version, chunk in rows:
            if (
                document.id in used_documents
                or evidence.quote not in chunk.content
                or len(origin.normalized_name) < 3
                or len(destination.normalized_name) < 3
                or len(evidence.relation_type) < 2
            ):
                continue
            if evidence_review_reason(
                evidence.quote,
                evidence.relation_type,
                origin.entity_type,
                destination.entity_type,
            ):
                continue
            used_documents.add(document.id)
            cases.append(
                {
                    "question": f"{origin.name}与{destination.name}是什么关系？",
                    "expected_document_ids": [document.id],
                    "expected_graph_evidence_ids": [evidence.id],
                    "tags": ["relation", "auto_labeled"],
                }
            )
            if len(cases) >= args.limit:
                break
        if not cases:
            raise SystemExit("No eligible current-version evidence")
        with TemporaryDirectory(prefix="pkb-graph-eval-") as directory:
            dataset = Path(directory) / "questions.jsonl"
            dataset.write_text(
                "\n".join(json.dumps(case, ensure_ascii=False) for case in cases), encoding="utf-8"
            )
            result = evaluate(db, kb_id, dataset)
        print(
            json.dumps(
                {
                    "kb_id": kb_id,
                    "sample_kind": "auto_labeled_smoke",
                    "sampled_questions": len(cases),
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
