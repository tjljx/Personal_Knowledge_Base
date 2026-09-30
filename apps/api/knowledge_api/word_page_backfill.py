"""Repair legacy Word citation page numbers without rebuilding chunks or model indexes."""

import argparse
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.db import get_engine
from knowledge_api.file_storage import blob_path
from knowledge_api.models import (
    ChatMessage,
    Document,
    DocumentChunk,
    DocumentParseResult,
    DocumentVersion,
)
from knowledge_api.parsers import _render_word_pages


def normalize(text: str) -> str:
    return "".join(char.casefold() for char in text if char.isalnum())


def locate_pages(chunks: list[DocumentChunk], pages: list[dict]) -> dict[str, int | None]:
    page_texts = [
        (page["number"], normalize(" ".join(block["text"] for block in page["blocks"])))
        for page in pages
    ]
    result = {}
    for chunk in chunks:
        content = normalize(chunk.content)
        if not content:
            result[chunk.id] = None
            continue
        width = min(18, len(content))
        starts = sorted({int((len(content) - width) * ratio / 4) for ratio in range(5)})
        anchors = {content[start : start + width] for start in starts}
        scores = Counter()
        for page_no, page_text in page_texts:
            scores[page_no] = sum(anchor in page_text for anchor in anchors)
        ranked = scores.most_common(2)
        best = ranked[0] if ranked else (None, 0)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        result[chunk.id] = best[0] if best[1] >= 1 and best[1] > runner_up else None
    return result


def backfill(*, apply: bool = False, document_id: str | None = None) -> None:
    totals = Counter()
    with Session(get_engine()) as db:
        query = select(Document).where(
            Document.file_type.in_(["doc", "docx"]), Document.status == "PARSED"
        )
        if document_id:
            query = query.where(Document.id == document_id)
        documents = db.scalars(query).all()
        for document in documents:
            version = db.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version_no == document.current_version,
                    DocumentVersion.status == "PARSED",
                )
            )
            if version is None:
                continue
            result = db.get(DocumentParseResult, version.id)
            if (
                result is None
                or result.structure.get("parser_config", {}).get("pagination")
                == "rendered_pdf_backfill"
            ):
                continue
            pages = _render_word_pages(blob_path(version.sha256), "." + document.file_type)
            if not pages:
                print("SKIP", document.id, "render unavailable", document.title[:50], flush=True)
                totals["skipped"] += 1
                continue
            chunks = db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == version.id)
                .order_by(DocumentChunk.ordinal)
            ).all()
            mapping = locate_pages(chunks, pages)
            matched = sum(page is not None for page in mapping.values())
            totals["documents"] += 1
            totals["chunks"] += len(chunks)
            totals["matched"] += matched
            print(
                "APPLY" if apply else "DRY",
                document.id,
                "pages",
                len(pages),
                "matched",
                matched,
                "/",
                len(chunks),
                document.title[:50],
                flush=True,
            )
            if not apply:
                continue
            for chunk in chunks:
                chunk.page_no = mapping[chunk.id]
            structure = dict(result.structure)
            structure["pages"] = pages
            structure["parser_config"] = {
                **structure.get("parser_config", {}),
                "pagination": "rendered_pdf_backfill",
                "rendered_page_count": len(pages),
            }
            result.structure = structure
            messages = db.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")).all()
            for message in messages:
                updated = False
                citations = []
                for citation in message.citations:
                    item = dict(citation)
                    if item.get("chunk_id") in mapping:
                        item["page_no"] = mapping[item["chunk_id"]]
                        updated = True
                    citations.append(item)
                if updated:
                    message.citations = citations
            db.commit()
    print("TOTAL", dict(totals), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--document-id")
    arguments = parser.parse_args()
    backfill(apply=arguments.apply, document_id=arguments.document_id)
