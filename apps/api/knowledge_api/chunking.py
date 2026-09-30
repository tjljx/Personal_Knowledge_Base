"""Deterministic, version-bound chunks for document retrieval and citations."""

from dataclasses import dataclass

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    page_no: int | None
    block_type: str
    content: str


def _split_text(text: str) -> list[str]:
    text = " ".join(text.split())
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + CHUNK_SIZE // 2:
                end = boundary
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def build_chunks(structure: dict) -> list[Chunk]:
    """Create ordered, page-aware chunks without crossing page boundaries."""
    chunks = []
    ordinal = 0
    for page in structure.get("pages", []):
        page_no = page.get("number")
        for block in page.get("blocks", []):
            block_type = str(block.get("type", "paragraph"))
            for part in _split_text(str(block.get("text", ""))):
                chunks.append(
                    Chunk(
                        ordinal=ordinal,
                        page_no=page_no if isinstance(page_no, int) else None,
                        block_type=block_type,
                        content=part,
                    )
                )
                ordinal += 1
    return chunks
