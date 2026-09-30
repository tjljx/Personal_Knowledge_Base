"""Persistent local document parsing worker."""

import argparse
import copy
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from knowledge_api.chunking import build_chunks
from knowledge_api.cloud_budget import BudgetExceeded, BudgetNotConfigured, reconcile_stale_calls
from knowledge_api.config import get_settings
from knowledge_api.db import get_engine
from knowledge_api.document_lifecycle import queue_chunk_deletions, reconcile_next_projection
from knowledge_api.embeddings import EmbeddingError, embed_texts
from knowledge_api.file_storage import blob_path
from knowledge_api.knowledge_graph import backfill_next_graph
from knowledge_api.models import (
    AuditLog,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentParseJob,
    DocumentParseResult,
    DocumentVersion,
    GraphChunkExtraction,
    GraphEvidence,
    KnowledgeBase,
    WorkerHeartbeat,
)
from knowledge_api.parsers import SUPPORTED_PARSE_EXTENSIONS, ParseError, parse_file
from knowledge_api.semantic_chunking import MAX_CHUNK, build_semantic_chunks
from knowledge_api.vector_store import VectorStoreError, upsert_vectors

logger = logging.getLogger("knowledge_api.worker")
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=15)
HEARTBEAT_NAME = "parse-worker"
# The heartbeat thread beats every 30s; three misses mean something is wrong.
HEARTBEAT_TIMEOUT_SECONDS = 90
HEARTBEAT_INTERVAL_SECONDS = 30
_prefer_graph_next = True
_activity = "starting"
_activity_since = time.monotonic()


def set_worker_activity(text: str) -> None:
    """Describe what the worker is doing right now, for /health."""
    global _activity, _activity_since
    _activity = text
    _activity_since = time.monotonic()


def worker_activity() -> str:
    """Current activity with elapsed time, so a long parse is visible as busy."""
    elapsed = int(time.monotonic() - _activity_since)
    if elapsed < 60:
        return _activity if _activity == "idle" else f"{_activity}（{elapsed}s）"
    minutes, seconds = divmod(elapsed, 60)
    if minutes < 60:
        return f"{_activity}（{minutes}m{seconds}s）"
    hours, minutes = divmod(minutes, 60)
    return f"{_activity}（{hours}h{minutes}m）"


def record_heartbeat(db: Session, detail: str = "") -> None:
    """Mark the worker as alive. Called by the heartbeat thread."""
    now = datetime.now(UTC)
    heartbeat = db.get(WorkerHeartbeat, HEARTBEAT_NAME)
    if heartbeat is None:
        db.add(WorkerHeartbeat(name=HEARTBEAT_NAME, updated_at=now, detail=detail[:200]))
    else:
        heartbeat.updated_at = now
        heartbeat.detail = detail[:200]
    db.commit()


def start_heartbeat_thread(
    interval: int = HEARTBEAT_INTERVAL_SECONDS,
    stop: "threading.Event | None" = None,
) -> "threading.Thread":
    """Beat independently of the work loop.

    A single 7000-page PDF can occupy the parser for hours; if the heartbeat were
    written by that loop the container would look dead while it is actually busy.
    """
    stop_event = stop or threading.Event()

    def beat() -> None:
        while not stop_event.is_set():
            try:
                with Session(get_engine()) as db:
                    record_heartbeat(db, worker_activity())
            except Exception:
                logger.exception("Heartbeat write failed")
            stop_event.wait(interval)

    thread = threading.Thread(target=beat, name="worker-heartbeat", daemon=True)
    thread.start()
    return thread


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps; PostgreSQL returns aware ones."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def heartbeat_age_seconds(db: Session) -> float | None:
    heartbeat = db.get(WorkerHeartbeat, HEARTBEAT_NAME)
    if heartbeat is None:
        return None
    return (datetime.now(UTC) - _as_utc(heartbeat.updated_at)).total_seconds()


def heartbeat_is_fresh(db: Session, timeout_seconds: int = HEARTBEAT_TIMEOUT_SECONDS) -> bool:
    age = heartbeat_age_seconds(db)
    return age is not None and age <= timeout_seconds


def replace_chunks(db: Session, version_id: str, structure: dict, chunks=None) -> None:
    chunk_ids = select(DocumentChunk.id).where(DocumentChunk.document_version_id == version_id)
    queue_chunk_deletions(db, list(db.scalars(chunk_ids)))
    db.execute(delete(GraphEvidence).where(GraphEvidence.chunk_id.in_(chunk_ids)))
    db.execute(delete(GraphChunkExtraction).where(GraphChunkExtraction.chunk_id.in_(chunk_ids)))
    db.execute(
        delete(DocumentChunkEmbedding).where(
            DocumentChunkEmbedding.chunk_id.in_(
                select(DocumentChunk.id).where(DocumentChunk.document_version_id == version_id)
            )
        )
    )
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version_id))
    db.add_all(
        [
            DocumentChunk(
                document_version_id=version_id,
                ordinal=chunk.ordinal,
                page_no=chunk.page_no,
                block_type=chunk.block_type,
                content=chunk.content,
            )
            for chunk in (chunks if chunks is not None else build_chunks(structure))
        ]
    )


def backfill_next_chunks(db: Session) -> bool:
    result = db.scalar(
        select(DocumentParseResult)
        .where(
            ~DocumentParseResult.document_version_id.in_(select(DocumentChunk.document_version_id))
        )
        .order_by(DocumentParseResult.created_at, DocumentParseResult.document_version_id)
        .limit(1)
    )
    if result is None:
        return False
    replace_chunks(db, result.document_version_id, result.structure)
    db.commit()
    return True


def backfill_next_semantic(db: Session) -> bool:
    """Upgrade existing parsed documents after their knowledge base enables cloud access."""
    settings = get_settings()
    if settings.model_api_key is None:
        return False
    rows = db.execute(
        select(DocumentParseResult, DocumentVersion, Document, KnowledgeBase)
        .join(DocumentVersion, DocumentVersion.id == DocumentParseResult.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(
            KnowledgeBase.cloud_enabled.is_(True),
            Document.status != "DELETED",
            Document.current_version == DocumentVersion.version_no,
            DocumentVersion.status == "PARSED",
            or_(
                DocumentParseResult.structure["chunking_method"].as_string().is_(None),
                DocumentParseResult.structure["chunking_method"].as_string()
                == "local_cloud_disabled",
            ),
        )
        .order_by(DocumentParseResult.created_at, DocumentParseResult.document_version_id)
    )
    for result, version, document, kb in rows:
        method = result.structure.get("chunking_method")
        if method not in {None, "local_cloud_disabled"}:
            continue
        structure = copy.deepcopy(result.structure)
        total_chars = sum(
            len(str(block.get("text", "")))
            for page in structure.get("pages", [])
            for block in page.get("blocks", [])
        )
        if total_chars <= MAX_CHUNK:
            structure["chunking_method"] = "local_short_document"
            result.structure = structure
            db.commit()
            return True
        try:
            chunks = build_semantic_chunks(
                db, settings, kb.owner_id, kb.id, structure, document.title
            )
        except (BudgetNotConfigured, BudgetExceeded) as exc:
            logger.info("Semantic backfill budget unavailable for %s: %s", version.id, exc)
            structure["chunking_method"] = "local_budget_fallback"
            result.structure = structure
            db.commit()
            return False
        replace_chunks(db, version.id, structure, chunks)
        result.structure = structure
        db.commit()
        return True
    return False


def backfill_next_embeddings(db: Session) -> bool:
    """Index existing and newly parsed chunks in small batches when a model is configured."""
    settings = get_settings()
    if not settings.embedding_model:
        return False
    rows = db.execute(
        select(DocumentChunk, Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .outerjoin(DocumentChunkEmbedding, DocumentChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            Document.current_version == DocumentVersion.version_no,
            Document.status != "DELETED",
            DocumentVersion.status == "PARSED",
            or_(
                DocumentChunkEmbedding.chunk_id.is_(None),
                DocumentChunkEmbedding.model != settings.embedding_model,
            ),
        )
        .order_by(DocumentChunk.created_at, DocumentChunk.id)
        .limit(16)
    ).all()
    if not rows:
        return False
    try:
        vectors = embed_texts(settings, [chunk.content for chunk, _, _ in rows])
    except EmbeddingError:
        logger.warning("Local embedding service unavailable; retrying on next worker cycle")
        return False
    if settings.vector_store_url:
        try:
            upsert_vectors(
                settings,
                [
                    {
                        "id": chunk.id,
                        "vector": vector,
                        "payload": {
                            "knowledge_base_id": document.knowledge_base_id,
                            "document_id": document.id,
                            "document_version_id": version.id,
                            "model": settings.embedding_model,
                        },
                    }
                    for (chunk, document, version), vector in zip(rows, vectors, strict=True)
                ],
            )
        except VectorStoreError:
            logger.warning("Qdrant unavailable; retrying embedding batch on next worker cycle")
            return False
    for (chunk, _, _), vector in zip(rows, vectors, strict=True):
        row = db.get(DocumentChunkEmbedding, chunk.id)
        if row is None:
            db.add(
                DocumentChunkEmbedding(
                    chunk_id=chunk.id, model=settings.embedding_model, vector=vector
                )
            )
        else:
            row.model = settings.embedding_model
            row.vector = vector
    db.commit()
    return True


def enqueue_parse(
    db: Session, version: DocumentVersion, user_id: str | None, force: bool = False
) -> DocumentParseJob:
    from pathlib import Path

    if Path(version.original_filename).suffix.lower() not in SUPPORTED_PARSE_EXTENSIONS:
        raise ValueError("此格式尚未接入解析器")
    job = db.scalar(
        select(DocumentParseJob).where(DocumentParseJob.document_version_id == version.id)
    )
    if job is None:
        job = DocumentParseJob(document_version_id=version.id, requested_by=user_id)
        db.add(job)
        if force:
            version.status = "UPLOADED"
            version.parse_error = None
    elif force:
        if job.status in {"QUEUED", "RUNNING"}:
            raise ValueError("解析任务正在进行中")
        job.status = "QUEUED"
        job.attempts = 0
        job.started_at = None
        job.requested_by = user_id
        job.updated_at = datetime.now(UTC)
        version.status = "UPLOADED"
        version.parse_error = None
    elif job.status == "CANCELLED" or (job.status == "FAILED" and job.attempts < MAX_ATTEMPTS):
        job.status = "QUEUED"
        job.requested_by = user_id
        job.updated_at = datetime.now(UTC)
        version.status = "UPLOADED"
        version.parse_error = None
    return job


def process_next(db: Session) -> bool:
    global _prefer_graph_next
    if reconcile_stale_calls(db, get_settings()):
        return True
    now = datetime.now(UTC)
    job = db.scalar(
        select(DocumentParseJob)
        .where(
            or_(
                DocumentParseJob.status == "QUEUED",
                (DocumentParseJob.status == "RUNNING")
                & (DocumentParseJob.started_at < now - STALE_AFTER),
            )
        )
        .order_by(DocumentParseJob.created_at, DocumentParseJob.id)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        if reconcile_next_projection(db):
            return True
        if backfill_next_chunks(db):
            return True
        if backfill_next_semantic(db):
            return True
        # Keep both search indexing and graph extraction moving after large imports.
        graph_first = _prefer_graph_next
        _prefer_graph_next = not graph_first
        tasks = (
            (backfill_next_graph, backfill_next_embeddings)
            if graph_first
            else (backfill_next_embeddings, backfill_next_graph)
        )
        for task in tasks:
            set_worker_activity(f"running {task.__name__}")
            if task(db):
                return True
        return False
    version = db.get(DocumentVersion, job.document_version_id)
    document = db.get(Document, version.document_id)
    if document.status == "DELETED":
        job.status = "CANCELLED"
        job.updated_at = now
        db.commit()
        return True
    if job.attempts >= MAX_ATTEMPTS:
        job.status = "FAILED"
        version.status = "FAILED"
        version.parse_error = "解析重试次数已达上限"
        if document.current_version == version.version_no and document.status != "DELETED":
            document.status = "FAILED"
        db.commit()
        return True
    job.status = "RUNNING"
    job.attempts += 1
    job.started_at = now
    job.updated_at = now
    version.status = "PARSING"
    version.parse_error = None
    if document.current_version == version.version_no and document.status != "DELETED":
        document.status = "PARSING"
    db.commit()

    set_worker_activity(f"parsing {version.original_filename}")
    try:
        output = parse_file(blob_path(version.sha256), version.original_filename)
    except ParseError as exc:
        error = str(exc)
    except Exception:
        logger.exception("Unexpected parse failure for job %s", job.id)
        error = "解析失败，请检查文件内容"
    else:
        error = None

    # The parse just ran in this session, so these objects are cached. Another
    # process may have cancelled or deleted the document meanwhile — expire them
    # to read the real state instead of the copy this session already holds.
    parsed_job_id = job.id
    db.expire_all()
    job = db.get(DocumentParseJob, parsed_job_id)
    version = db.get(DocumentVersion, job.document_version_id)
    document = db.get(Document, version.document_id)
    if document.status == "DELETED" or job.status == "CANCELLED":
        # Deleted or cancelled while this parse was running: drop the result and
        # keep the state the delete/cancel request already restored.
        job.status = "CANCELLED"
        job.updated_at = datetime.now(UTC)
        db.commit()
        return True
    now = datetime.now(UTC)
    job.updated_at = now
    if error:
        job.status = "FAILED"
        version.status = "FAILED"
        version.parse_error = error[:500]
        if document.current_version == version.version_no and document.status != "DELETED":
            document.status = "FAILED"
    else:
        kb = db.get(KnowledgeBase, document.knowledge_base_id)
        settings = get_settings()
        chunks = None
        if output.parser_name.startswith("pypdf") and len(output.structure.get("pages", [])) > 200:
            output.structure["chunking_method"] = "local_large_pdf"
            chunks = build_chunks(output.structure)
        elif kb.cloud_enabled and settings.model_api_key is not None:
            try:
                chunks = build_semantic_chunks(
                    db, settings, kb.owner_id, kb.id, output.structure, document.title
                )
            except (BudgetNotConfigured, BudgetExceeded) as exc:
                logger.info("Semantic parsing budget unavailable for %s: %s", version.id, exc)
                output.structure["chunking_method"] = "local_budget_fallback"
        else:
            output.structure["chunking_method"] = "local_cloud_disabled"
        existing = db.get(DocumentParseResult, version.id)
        if existing is None:
            db.add(
                DocumentParseResult(
                    document_version_id=version.id,
                    parser_name=output.parser_name,
                    parser_version=output.parser_version,
                    markdown=output.markdown,
                    structure=output.structure,
                )
            )
        else:
            existing.parser_name = output.parser_name
            existing.parser_version = output.parser_version
            existing.markdown = output.markdown
            existing.structure = output.structure
        replace_chunks(db, version.id, output.structure, chunks)
        job.status = "SUCCEEDED"
        version.status = "PARSED"
        version.parse_error = None
        if document.current_version == version.version_no and document.status != "DELETED":
            document.status = "PARSED"
    db.add(
        AuditLog(
            user_id=job.requested_by,
            action="parse_document_version" if not error else "parse_document_failed",
            resource_type="document",
            resource_id=document.id,
            detail=f"version_id={version.id};attempt={job.attempts}",
        )
    )
    db.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued document parsing jobs")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info(
        "Worker started (heartbeat every %ss, timeout %ss)",
        HEARTBEAT_INTERVAL_SECONDS,
        HEARTBEAT_TIMEOUT_SECONDS,
    )
    if not args.once:
        # Runs for the whole process lifetime, including during a long parse.
        start_heartbeat_thread()
    while True:
        try:
            with Session(get_engine()) as db:
                processed = process_next(db)
            set_worker_activity("idle")
            if args.once:
                with Session(get_engine()) as db:
                    record_heartbeat(db, worker_activity())
        except Exception:
            logger.exception("Worker cycle failed")
            processed = False
        if args.once:
            break
        if not processed:
            time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    main()
