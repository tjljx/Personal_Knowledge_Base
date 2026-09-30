import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from knowledge_api.cloud_budget import (
    BudgetExceeded,
    BudgetNotConfigured,
    monthly_summary,
    reserve_call,
    settle_call,
)
from knowledge_api.config import get_settings
from knowledge_api.db import get_db
from knowledge_api.document_lifecycle import queue_document_projection
from knowledge_api.file_storage import blob_path, save_upload
from knowledge_api.knowledge_graph import graph_output, graph_progress
from knowledge_api.model_gateway import (
    ModelAnswer,
    ModelGatewayError,
    Source,
    answer_with_sources,
    stream_answer_with_sources,
)
from knowledge_api.models import (
    AuditLog,
    AuthSession,
    ChatMessage,
    ChatSession,
    Document,
    DocumentChunk,
    DocumentParseJob,
    DocumentParseResult,
    DocumentTag,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeBaseMember,
    LoginAttempt,
    RevokedToken,
    User,
)
from knowledge_api.parsers import SUPPORTED_PARSE_EXTENSIONS
from knowledge_api.retrieval import retrieve_chunks
from knowledge_api.schemas import (
    AskInput,
    AskOutput,
    ChangePasswordInput,
    ChatMessageInput,
    ChatMessageOutput,
    ChatSessionCreate,
    ChatSessionOutput,
    ChunkSearchResult,
    CitationSourceOutput,
    CloudAccessUpdate,
    DocumentBatchOutput,
    DocumentMetadataUpdate,
    DocumentOutput,
    DocumentParseIssueOutput,
    DocumentParseJobOutput,
    DocumentParseOverviewOutput,
    DocumentParseResultOutput,
    DocumentSearchResult,
    DocumentVersionOutput,
    KnowledgeBaseCreate,
    KnowledgeBaseOutput,
    LoginInput,
    MemberCreate,
    ProvenanceChunk,
    ProvenanceOutput,
    ProvenanceVersion,
    TokenOutput,
    UserCreate,
    UserOutput,
)
from knowledge_api.security import (
    admin_user,
    current_claims,
    current_user,
    hash_password,
    issue_token,
    verify_password,
)
from knowledge_api.worker import MAX_ATTEMPTS, enqueue_parse

logger = logging.getLogger("knowledge_api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="个人知识库 API", version="0.1.0")


def _verified_page_no(db: Session, version: DocumentVersion, page_no: int | None) -> int | None:
    if page_no is None:
        return None
    result = db.get(DocumentParseResult, version.id)
    if result is None:
        return None
    if (
        result.parser_name in {"docx-basic", "libreoffice-docx", "markdown-basic", "html-basic"}
        and result.structure.get("parser_config", {}).get("pagination") != "rendered_pdf_backfill"
    ):
        return None
    return page_no


def _citation_record(
    db: Session,
    number: int,
    document: Document,
    version: DocumentVersion,
    chunk: DocumentChunk,
    retrieval: dict | None = None,
) -> dict:
    return {
        "number": number,
        "document_id": document.id,
        "document_title": document.title,
        "version_no": version.version_no,
        "page_no": _verified_page_no(db, version, chunk.page_no),
        "chunk_id": chunk.id,
        "quote": chunk.content[:320],
        "graph_evidence": (retrieval or {}).get("graph_evidence", []),
    }


def _answer_source(chunk: DocumentChunk, retrieval: dict | None) -> str:
    clues = (retrieval or {}).get("graph_evidence", [])
    if not clues:
        return chunk.content
    relations = "\n".join(
        f"- {item['source']} —{item['relation']}→ {item['target']}；原文摘录：{item['quote'][:240]}"
        for item in clues
    )
    return f"原文片段：\n{chunk.content}\n\n从本片段抽取的关系线索（请以原文核实）：\n{relations}"


def _current_citation(db: Session, citation: dict) -> dict:
    item = dict(citation)
    chunk = db.get(DocumentChunk, item.get("chunk_id")) if item.get("chunk_id") else None
    if chunk is None:
        item["page_no"] = None
        return item
    version = db.get(DocumentVersion, chunk.document_version_id)
    item["page_no"] = _verified_page_no(db, version, chunk.page_no) if version else None
    return item


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/citations/{chunk_id}",
    response_model=CitationSourceOutput,
)
def get_citation_source(
    kb_id: UUID,
    chunk_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chunk = db.get(DocumentChunk, str(chunk_id))
    if chunk is None:
        raise HTTPException(status_code=404, detail="引用片段不存在")
    version = db.get(DocumentVersion, chunk.document_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="引用版本不存在")
    document = visible_document(db, str(kb_id), version.document_id, user)
    return {
        **_citation_record(db, 0, document, version, chunk),
        "content": chunk.content,
        "file_type": document.file_type,
        "original_filename": version.original_filename,
    }


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    request_id = str(uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            ensure_ascii=False,
        )
    )
    return response


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="数据库不可用") from None
    return {"application": "ok", "database": "ok"}


@app.post("/api/v1/auth/login", response_model=TokenOutput)
def login(data: LoginInput, request: Request, db: Session = Depends(get_db)):
    username = data.username.strip().lower()
    ip_address = request.client.host if request.client else "unknown"
    cutoff = datetime.now(UTC) - timedelta(minutes=15)
    failures = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.created_at >= cutoff,
            or_(LoginAttempt.username == username, LoginAttempt.ip_address == ip_address),
        )
    )
    if failures >= 5:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后重试")

    user = db.scalar(select(User).where(User.username == username))
    if (
        user is None
        or user.status != "ACTIVE"
        or not verify_password(data.password, user.password_hash)
    ):
        db.add(LoginAttempt(username=username, ip_address=ip_address))
        db.commit()
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    now = datetime.now(UTC)
    db.execute(
        delete(AuthSession).where(
            AuthSession.last_activity_at
            <= now - timedelta(minutes=get_settings().session_idle_minutes)
        )
    )
    auth_session = AuthSession(user_id=user.id, last_activity_at=now)
    db.add(auth_session)
    db.add(AuditLog(user_id=user.id, action="login", resource_type="user", resource_id=user.id))
    db.commit()
    return TokenOutput(access_token=issue_token(user, auth_session.id))


@app.post("/api/v1/auth/logout", status_code=204)
def logout(
    claims: dict = Depends(current_claims),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    auth_session = db.get(AuthSession, claims["sid"])
    if auth_session is not None:
        db.delete(auth_session)
    db.add(
        RevokedToken(
            jti=claims["jti"],
            expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        )
    )
    db.add(AuditLog(user_id=user.id, action="logout", resource_type="user", resource_id=user.id))
    db.commit()
    return Response(status_code=204)


@app.get("/api/v1/auth/me", response_model=UserOutput)
def me(user: User = Depends(current_user)):
    return user


@app.post("/api/v1/auth/renew", response_model=TokenOutput)
def renew_token(
    claims: dict = Depends(current_claims),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    auth_session = db.get(AuthSession, claims["sid"])
    auth_session.last_activity_at = datetime.now(UTC)
    db.commit()
    return TokenOutput(access_token=issue_token(user, auth_session.id))


@app.post("/api/v1/auth/change-password", status_code=204)
def change_password(
    data: ChangePasswordInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(data.new_password)
    user.auth_version += 1
    db.add(
        AuditLog(
            user_id=user.id, action="change_password", resource_type="user", resource_id=user.id
        )
    )
    db.commit()
    return Response(status_code=204)


@app.get("/api/v1/users", response_model=list[UserOutput])
def list_users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.username)).all()


@app.post("/api/v1/users", response_model=UserOutput, status_code=201)
def create_user(data: UserCreate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = User(username=data.username.lower(), password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from None
    db.add(
        AuditLog(user_id=admin.id, action="create_user", resource_type="user", resource_id=user.id)
    )
    db.commit()
    db.refresh(user)
    return user


def visible_kb(db: Session, kb_id: str, user: User) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.status != "ACTIVE":
        raise HTTPException(status_code=404, detail="知识库不存在")
    if user.role == "ADMIN" or kb.owner_id == user.id:
        return kb
    membership = db.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == kb_id,
            KnowledgeBaseMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def writable_kb(db: Session, kb_id: str, user: User) -> KnowledgeBase:
    kb = visible_kb(db, kb_id, user)
    if user.role == "ADMIN" or kb.owner_id == user.id:
        return kb
    role = db.scalar(
        select(KnowledgeBaseMember.member_role).where(
            KnowledgeBaseMember.knowledge_base_id == kb_id,
            KnowledgeBaseMember.user_id == user.id,
        )
    )
    if role != "EDITOR":
        raise HTTPException(status_code=403, detail="需要知识库编辑权限")
    return kb


def visible_document(
    db: Session, kb_id: str, doc_id: str, user: User, include_deleted: bool = False
) -> Document:
    visible_kb(db, kb_id, user)
    document = db.get(Document, doc_id)
    if (
        document is None
        or document.knowledge_base_id != kb_id
        or (document.status == "DELETED" and not include_deleted)
    ):
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


def search_snippet(markdown: str, query: str) -> str:
    position = markdown.casefold().find(query.casefold())
    if position < 0:
        return ""
    start = max(0, position - 90)
    end = min(len(markdown), position + len(query) + 150)
    snippet = " ".join(markdown[start:end].split())
    return f"…{snippet}" if start else snippet


def search_locations(structure: dict, query: str) -> list[dict]:
    locations = []
    folded = query.casefold()
    for page in structure.get("pages", []):
        for block in page.get("blocks", []):
            if folded in str(block.get("text", "")).casefold():
                locations.append({"page": page.get("number"), "block_type": block.get("type")})
                break
        if len(locations) == 3:
            break
    return locations


def retrieval_terms(question: str) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9_]{2,}", question.casefold())
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        terms.extend(phrase[index : index + 2] for index in range(len(phrase) - 1))
    common_words = {
        "what",
        "when",
        "where",
        "which",
        "about",
        "the",
        "and",
        "for",
        "什么",
        "多少",
        "如何",
        "是否",
        "这个",
        "那个",
        "它的",
        "请问",
        "帮我",
    }
    return list(
        dict.fromkeys(term for term in terms if len(term) >= 2 and term not in common_words)
    )[:20]


def answer_question(
    kb_id: UUID,
    data: AskInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    history: list[dict[str, str]] | None = None,
):
    """Answer from authorized, current-version source chunks with mandatory citations."""
    kb = visible_kb(db, str(kb_id), user)
    question = data.question.strip()
    current_terms = retrieval_terms(question)
    if not current_terms:
        raise HTTPException(status_code=422, detail="问题需要至少包含两个连续的文字或数字")
    previous_question = next(
        (
            message["content"]
            for message in reversed(history or [])
            if message.get("role") == "user"
        ),
        "",
    )
    context_terms = [
        term for term in retrieval_terms(previous_question) if term not in current_terms
    ][:20]
    trace: dict = {}
    ranked = retrieve_chunks(db, str(kb_id), question, current_terms, context_terms, trace=trace)
    if not ranked:
        return {"answer": "根据当前可引用资料，无法可靠回答这个问题。", "citations": []}
    if not kb.cloud_enabled:
        raise HTTPException(status_code=403, detail="此知识库尚未由管理员启用云端问答")
    sources = [
        Source(number=index, content=_answer_source(row[2], trace.get(row[2].id)))
        for index, row in enumerate(ranked, 1)
    ]
    settings = get_settings()
    try:
        usage = reserve_call(db, settings, user.id, str(kb_id), question, sources, history)
    except BudgetNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    try:
        model_answer = answer_with_sources(settings, question, sources, history)
    except ModelGatewayError as exc:
        settle_call(db, settings, usage, None)
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except Exception:
        settle_call(db, settings, usage, None)
        raise
    settle_call(db, settings, usage, model_answer)
    answer = model_answer.content
    cited_numbers = list(
        dict.fromkeys(int(value) for value in re.findall(r"\[(\d{1,2})\]", answer))
    )
    if not cited_numbers or any(number < 1 or number > len(ranked) for number in cited_numbers):
        return {"answer": "根据当前可引用资料，无法可靠回答这个问题。", "citations": []}
    return {
        "answer": answer,
        "citations": [
            _citation_record(db, index, document, version, chunk, trace.get(chunk.id))
            for index, (document, version, chunk) in enumerate(ranked, 1)
            if index in cited_numbers
        ],
    }


@app.get("/api/v1/model-usage/monthly")
def get_monthly_model_usage(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    return monthly_summary(db, get_settings())


@app.post("/api/v1/knowledge-bases/{kb_id}/ask", response_model=AskOutput)
def ask_knowledge_base(
    kb_id: UUID,
    data: AskInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return answer_question(kb_id, data, user, db)


def visible_chat_session(db: Session, kb_id: str, session_id: str, user: User) -> ChatSession:
    visible_kb(db, kb_id, user)
    session = db.get(ChatSession, session_id)
    if session is None or session.knowledge_base_id != kb_id or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    return session


@app.get("/api/v1/knowledge-bases/{kb_id}/chat-sessions", response_model=list[ChatSessionOutput])
def list_chat_sessions(
    kb_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    visible_kb(db, str(kb_id), user)
    return db.scalars(
        select(ChatSession)
        .where(ChatSession.knowledge_base_id == str(kb_id), ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
    ).all()


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/chat-sessions",
    response_model=ChatSessionOutput,
    status_code=201,
)
def create_chat_session(
    kb_id: UUID,
    data: ChatSessionCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_kb(db, str(kb_id), user)
    session = ChatSession(knowledge_base_id=str(kb_id), user_id=user.id, title=data.title.strip())
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages",
    response_model=list[ChatMessageOutput],
)
def list_chat_messages(
    kb_id: UUID,
    session_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_chat_session(db, str(kb_id), str(session_id), user)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == str(session_id))
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    return [
        {
            **ChatMessageOutput.model_validate(message).model_dump(),
            "citations": [_current_citation(db, citation) for citation in message.citations],
        }
        for message in messages
    ]


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages",
    response_model=ChatMessageOutput,
)
def send_chat_message(
    kb_id: UUID,
    session_id: UUID,
    data: ChatMessageInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = visible_chat_session(db, str(kb_id), str(session_id), user)
    previous = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(8)
    ).all()
    history = [{"role": message.role, "content": message.content} for message in reversed(previous)]
    result = answer_question(kb_id, AskInput(question=data.question), user, db, history)
    now = datetime.now(UTC)
    db.add(
        ChatMessage(session_id=session.id, role="user", content=data.question.strip(), citations=[])
    )
    answer = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=result["answer"],
        citations=result["citations"],
        model=get_settings().model_name,
    )
    db.add(answer)
    if session.title == "新对话":
        session.title = data.question.strip()[:80]
    session.updated_at = now
    db.commit()
    db.refresh(answer)
    return answer


def _stream_event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages/stream")
def stream_chat_message(
    kb_id: UUID,
    session_id: UUID,
    data: ChatMessageInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_chat_session(db, str(kb_id), str(session_id), user)
    question = data.question.strip()
    user_id = user.id
    kb_key = str(kb_id)
    session_key = str(session_id)
    engine = db.get_bind()

    def generate():
        yield _stream_event("start", {"session_id": session_key})
        with Session(engine) as stream_db:
            session = visible_chat_session(
                stream_db, kb_key, session_key, stream_db.get(User, user_id)
            )
            history_rows = stream_db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(8)
            ).all()
            history = [{"role": row.role, "content": row.content} for row in reversed(history_rows)]
            terms = retrieval_terms(question)
            if not terms:
                yield _stream_event("error", {"message": "问题需要至少包含两个连续的文字或数字"})
                return
            previous_question = next(
                (message["content"] for message in reversed(history) if message["role"] == "user"),
                "",
            )
            context_terms = [
                term for term in retrieval_terms(previous_question) if term not in terms
            ][:20]
            trace: dict = {}
            ranked = retrieve_chunks(stream_db, kb_key, question, terms, context_terms, trace=trace)
            kb = visible_kb(stream_db, kb_key, stream_db.get(User, user_id))
            if not ranked:
                answer_text = "根据当前可引用资料，无法可靠回答这个问题。"
                citations = []
            elif not kb.cloud_enabled:
                yield _stream_event("error", {"message": "此知识库尚未由管理员启用云端问答"})
                return
            else:
                sources = [
                    Source(number=index, content=_answer_source(row[2], trace.get(row[2].id)))
                    for index, row in enumerate(ranked, 1)
                ]
                settings = get_settings()
                try:
                    usage = reserve_call(
                        stream_db, settings, user_id, kb_key, question, sources, history
                    )
                except (BudgetNotConfigured, BudgetExceeded) as exc:
                    yield _stream_event("error", {"message": str(exc)})
                    return
                answer_parts = []
                token_usage = (None, None)
                completed = False
                try:
                    for kind, value in stream_answer_with_sources(
                        settings, question, sources, history
                    ):
                        if kind == "delta":
                            answer_parts.append(value)
                            yield _stream_event("delta", {"content": value})
                        elif kind == "usage":
                            token_usage = value
                    completed = True
                except ModelGatewayError as exc:
                    yield _stream_event("error", {"message": str(exc)})
                    return
                finally:
                    settle_call(
                        stream_db,
                        settings,
                        usage,
                        ModelAnswer("".join(answer_parts), *token_usage) if completed else None,
                    )
                answer_text = "".join(answer_parts).strip()
                cited_numbers = list(
                    dict.fromkeys(int(value) for value in re.findall(r"\[(\d{1,2})\]", answer_text))
                )
                if not cited_numbers or any(
                    number < 1 or number > len(ranked) for number in cited_numbers
                ):
                    answer_text = "根据当前可引用资料，无法可靠回答这个问题。"
                    citations = []
                else:
                    citations = [
                        _citation_record(
                            stream_db, index, document, version, chunk, trace.get(chunk.id)
                        )
                        for index, (document, version, chunk) in enumerate(ranked, 1)
                        if index in cited_numbers
                    ]
            now = datetime.now(UTC)
            stream_db.add(
                ChatMessage(session_id=session.id, role="user", content=question, citations=[])
            )
            stream_db.add(
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=answer_text,
                    citations=citations,
                    model=get_settings().model_name if ranked else None,
                )
            )
            if session.title == "新对话":
                session.title = question[:80]
            session.updated_at = now
            stream_db.commit()
            yield _stream_event("done", {"answer": answer_text, "citations": citations})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/knowledge-bases/{kb_id}/knowledge-graph")
def get_knowledge_graph(
    kb_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_kb(db, str(kb_id), user)
    return graph_output(db, str(kb_id))


@app.get("/api/v1/knowledge-bases/{kb_id}/knowledge-graph/progress")
def get_knowledge_graph_progress(
    kb_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_kb(db, str(kb_id), user)
    return graph_progress(db, str(kb_id))


@app.get("/api/v1/knowledge-bases/{kb_id}/search/chunks", response_model=list[ChunkSearchResult])
def search_chunks(
    kb_id: UUID,
    response: Response,
    q: str = Query(min_length=1, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Retrieve current-version source chunks within a knowledge base the user can access."""
    visible_kb(db, str(kb_id), user)
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="搜索关键词不能为空")
    current_version = DocumentVersion
    statement = (
        select(Document, current_version, DocumentChunk)
        .join(
            current_version,
            (current_version.document_id == Document.id)
            & (current_version.version_no == Document.current_version),
        )
        .join(DocumentChunk, DocumentChunk.document_version_id == current_version.id)
        .where(
            Document.knowledge_base_id == str(kb_id),
            Document.status != "DELETED",
            current_version.status == "PARSED",
            DocumentChunk.content.ilike(f"%{query}%"),
        )
        .order_by(Document.updated_at.desc(), Document.id.desc(), DocumentChunk.ordinal)
    )
    response.headers["X-Total-Count"] = str(
        db.scalar(select(func.count()).select_from(statement.subquery()))
    )
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return [
        {
            "document": document,
            "version_no": version.version_no,
            "chunk_id": chunk.id,
            "page_no": chunk.page_no,
            "block_type": chunk.block_type,
            "content": chunk.content,
        }
        for document, version, chunk in rows
    ]


@app.get("/api/v1/knowledge-bases/{kb_id}/search", response_model=list[DocumentSearchResult])
def search_documents(
    kb_id: UUID,
    response: Response,
    q: str = Query(min_length=1, max_length=100),
    tag: str | None = Query(default=None, max_length=50),
    source: str | None = Query(default=None, max_length=200),
    file_type: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Search current document metadata and parsed content in an authorized knowledge base."""
    visible_kb(db, str(kb_id), user)
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="搜索关键词不能为空")
    pattern = f"%{query}%"
    current_version = DocumentVersion
    criteria = [
        Document.knowledge_base_id == str(kb_id),
        Document.status != "DELETED",
        or_(
            Document.title.ilike(pattern),
            current_version.original_filename.ilike(pattern),
            (current_version.status == "PARSED") & DocumentParseResult.markdown.ilike(pattern),
        ),
    ]
    if tag:
        criteria.append(
            Document.id.in_(select(DocumentTag.document_id).where(DocumentTag.name == tag.strip()))
        )
    if source:
        criteria.append(Document.source == source.strip())
    if file_type:
        criteria.append(Document.file_type == file_type.strip().lower().lstrip("."))
    statement = (
        select(Document, current_version, DocumentParseResult)
        .join(
            current_version,
            (current_version.document_id == Document.id)
            & (current_version.version_no == Document.current_version),
        )
        .outerjoin(
            DocumentParseResult, DocumentParseResult.document_version_id == current_version.id
        )
        .where(*criteria)
        .order_by(Document.updated_at.desc(), Document.id.desc())
    )
    response.headers["X-Total-Count"] = str(
        db.scalar(select(func.count()).select_from(statement.subquery()))
    )
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    results = []
    for document, version, parsed in rows:
        matched_in = []
        if query.casefold() in document.title.casefold():
            matched_in.append("标题")
        if query.casefold() in version.original_filename.casefold():
            matched_in.append("文件名")
        if (
            version.status == "PARSED"
            and parsed is not None
            and query.casefold() in parsed.markdown.casefold()
        ):
            matched_in.append("正文")
        results.append(
            {
                "document": document,
                "matched_in": matched_in,
                "snippet": search_snippet(parsed.markdown, query)
                if parsed is not None and version.status == "PARSED"
                else "",
                "locations": search_locations(parsed.structure, query)
                if parsed is not None and version.status == "PARSED"
                else [],
            }
        )
    return results


@app.get("/api/v1/knowledge-bases/{kb_id}/documents", response_model=list[DocumentOutput])
def list_documents(
    kb_id: UUID,
    response: Response,
    q: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=50),
    source: str | None = Query(default=None, max_length=200),
    file_type: str | None = Query(default=None, max_length=20),
    deleted: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if deleted:
        writable_kb(db, str(kb_id), user)
    else:
        visible_kb(db, str(kb_id), user)
    criteria = [
        Document.knowledge_base_id == str(kb_id),
        Document.status == "DELETED" if deleted else Document.status != "DELETED",
    ]
    if q:
        pattern = f"%{q.strip()}%"
        criteria.append(
            or_(
                Document.title.ilike(pattern),
                Document.id.in_(
                    select(DocumentVersion.document_id).where(
                        DocumentVersion.original_filename.ilike(pattern)
                    )
                ),
            )
        )
    if tag:
        criteria.append(
            Document.id.in_(select(DocumentTag.document_id).where(DocumentTag.name == tag))
        )
    if source:
        criteria.append(Document.source == source)
    if file_type:
        criteria.append(Document.file_type == file_type.lower().lstrip("."))
    response.headers["X-Total-Count"] = str(
        db.scalar(select(func.count(Document.id)).where(*criteria))
    )
    return db.scalars(
        select(Document)
        .where(*criteria)
        .order_by(Document.updated_at.desc(), Document.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()


def create_document_record(
    db: Session, kb: KnowledgeBase, file: UploadFile, user: User
) -> Document:
    filename, content_type, digest, size = save_upload(file)
    document = Document(
        knowledge_base_id=kb.id,
        title=filename,
        file_type=Path(filename).suffix.lower().lstrip("."),
        created_by=user.id,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        original_filename=filename,
        content_type=content_type,
        sha256=digest,
        size_bytes=size,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    if Path(filename).suffix.lower() in SUPPORTED_PARSE_EXTENSIONS:
        enqueue_parse(db, version, user.id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="upload_document",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents", response_model=DocumentOutput, status_code=201
)
def upload_document(
    kb_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    kb = writable_kb(db, str(kb_id), user)
    return create_document_record(db, kb, file, user)


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents/batch",
    response_model=DocumentBatchOutput,
)
def upload_document_batch(
    kb_id: UUID,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    kb = writable_kb(db, str(kb_id), user)
    if len(files) > 10:
        raise HTTPException(status_code=422, detail="每批最多上传 10 个文件")
    items = []
    errors = []
    for file in files:
        try:
            items.append(create_document_record(db, kb, file, user))
        except HTTPException as exc:
            errors.append({"filename": file.filename or "", "detail": exc.detail})
    return {"items": items, "errors": errors}


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/documents/parse-issues",
    response_model=DocumentParseOverviewOutput,
)
def document_parse_issues(
    kb_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    visible_kb(db, str(kb_id), user)
    active = [Document.knowledge_base_id == str(kb_id), Document.status != "DELETED"]
    total = db.scalar(select(func.count(Document.id)).where(*active)) or 0
    parsed = (
        db.scalar(select(func.count(Document.id)).where(*active, Document.status == "PARSED")) or 0
    )
    rows = db.execute(
        select(Document, DocumentVersion, DocumentParseJob)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_no == Document.current_version),
        )
        .outerjoin(DocumentParseJob, DocumentParseJob.document_version_id == DocumentVersion.id)
        .where(*active, Document.status != "PARSED")
        .order_by(Document.updated_at.desc(), Document.id.desc())
    ).all()
    issues = [
        DocumentParseIssueOutput(
            document_id=doc.id,
            title=doc.title,
            version_no=version.version_no,
            original_filename=version.original_filename,
            status=doc.status,
            job_status=job.status if job else None,
            attempts=job.attempts if job else 0,
            parse_error=version.parse_error,
            supported=Path(version.original_filename).suffix.lower() in SUPPORTED_PARSE_EXTENSIONS,
        )
        for doc, version, job in rows
    ]
    return DocumentParseOverviewOutput(
        total_documents=total, parsed_documents=parsed, issues=issues
    )


@app.get("/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}", response_model=DocumentOutput)
def get_document(
    kb_id: UUID, doc_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return visible_document(db, str(kb_id), str(doc_id), user)


@app.delete("/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(
    kb_id: UUID, doc_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    writable_kb(db, str(kb_id), user)
    visible_document(db, str(kb_id), str(doc_id), user)
    document = db.scalar(
        select(Document)
        .where(Document.id == str(doc_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    document.status = "DELETED"
    document.updated_at = datetime.now(UTC)
    versions = db.scalars(
        select(DocumentVersion.id).where(DocumentVersion.document_id == document.id)
    ).all()
    for job in db.scalars(
        select(DocumentParseJob).where(
            DocumentParseJob.document_version_id.in_(versions),
            DocumentParseJob.status.in_(["QUEUED", "RUNNING"]),
        )
    ):
        job.status = "CANCELLED"
        job.updated_at = datetime.now(UTC)
    queue_document_projection(db, document.id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="delete_document",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/restore", response_model=DocumentOutput
)
def restore_document(
    kb_id: UUID, doc_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    writable_kb(db, str(kb_id), user)
    document = visible_document(db, str(kb_id), str(doc_id), user, include_deleted=True)
    document = db.scalar(
        select(Document)
        .where(Document.id == document.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document.status != "DELETED":
        raise HTTPException(status_code=409, detail="文档未被删除")
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == document.current_version,
        )
    )
    document.status = version.status
    document.updated_at = datetime.now(UTC)
    if (
        version.status in {"UPLOADED", "PARSING"}
        and Path(version.original_filename).suffix.lower() in SUPPORTED_PARSE_EXTENSIONS
    ):
        version.status = "UPLOADED"
        document.status = "UPLOADED"
        enqueue_parse(db, version, user.id)
    queue_document_projection(db, document.id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="restore_document",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document


@app.put("/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}", response_model=DocumentOutput)
def update_document_metadata(
    kb_id: UUID,
    doc_id: UUID,
    data: DocumentMetadataUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    writable_kb(db, str(kb_id), user)
    document = visible_document(db, str(kb_id), str(doc_id), user)
    tags = [tag.strip() for tag in data.tags]
    if any(not tag or len(tag) > 50 for tag in tags) or len(set(tags)) != len(tags):
        raise HTTPException(status_code=422, detail="标签不能为空、重复或超过 50 字符")
    document.title = data.title.strip()
    if not document.title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    document.description = data.description.strip()
    document.source = data.source.strip()
    document.tag_entries = [DocumentTag(name=tag) for tag in tags]
    document.updated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            user_id=user.id,
            action="update_document_metadata",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions",
    response_model=list[DocumentVersionOutput],
)
def list_document_versions(
    kb_id: UUID, doc_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    document = visible_document(db, str(kb_id), str(doc_id), user, include_deleted=True)
    if document.status == "DELETED":
        writable_kb(db, str(kb_id), user)
    return db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_no.desc())
    ).all()


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions/{version_no}/rollback",
    response_model=DocumentOutput,
)
def rollback_document_version(
    kb_id: UUID,
    doc_id: UUID,
    version_no: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    writable_kb(db, str(kb_id), user)
    visible_document(db, str(kb_id), str(doc_id), user)
    document = db.scalar(
        select(Document)
        .where(Document.id == str(doc_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id, DocumentVersion.version_no == version_no
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    if document.current_version == version_no:
        raise HTTPException(status_code=409, detail="该版本已是当前版本")
    previous = document.current_version
    document.current_version = version_no
    document.file_type = Path(version.original_filename).suffix.lower().lstrip(".")
    document.status = version.status
    document.updated_at = datetime.now(UTC)
    if version.status == "PARSING":
        version.status = "UPLOADED"
        document.status = "UPLOADED"
        enqueue_parse(db, version, user.id)
    queue_document_projection(db, document.id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="rollback_document_version",
            resource_type="document",
            resource_id=document.id,
            detail=f"from={previous};to={version_no}",
        )
    )
    db.commit()
    db.refresh(document)
    return document


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions",
    response_model=DocumentVersionOutput,
    status_code=201,
)
def upload_document_version(
    kb_id: UUID,
    doc_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    writable_kb(db, str(kb_id), user)
    visible_document(db, str(kb_id), str(doc_id), user)
    document = db.scalar(
        select(Document)
        .where(Document.id == str(doc_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == document.current_version,
        )
    )
    filename, content_type, digest, size = save_upload(file)
    if current.sha256 == digest:
        raise HTTPException(status_code=409, detail="文件内容与当前版本相同")
    document.current_version = (
        db.scalar(
            select(func.max(DocumentVersion.version_no)).where(
                DocumentVersion.document_id == document.id
            )
        )
        or 0
    ) + 1
    document.status = "UPLOADED"
    document.file_type = Path(filename).suffix.lower().lstrip(".")
    document.updated_at = datetime.now(UTC)
    version = DocumentVersion(
        document_id=document.id,
        version_no=document.current_version,
        original_filename=filename,
        content_type=content_type,
        sha256=digest,
        size_bytes=size,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    if Path(filename).suffix.lower() in SUPPORTED_PARSE_EXTENSIONS:
        enqueue_parse(db, version, user.id)
    queue_document_projection(db, document.id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="upload_document_version",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()
    db.refresh(version)
    return version


@app.post(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions/{version_no}/parse",
    response_model=DocumentParseJobOutput,
    status_code=202,
)
def request_document_parse(
    kb_id: UUID,
    doc_id: UUID,
    version_no: int,
    force: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    writable_kb(db, str(kb_id), user)
    document = visible_document(db, str(kb_id), str(doc_id), user)
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == version_no,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    if Path(version.original_filename).suffix.lower() not in SUPPORTED_PARSE_EXTENSIONS:
        raise HTTPException(status_code=415, detail="此格式尚未接入解析器")
    job = db.scalar(
        select(DocumentParseJob).where(DocumentParseJob.document_version_id == version.id)
    )
    if job is not None and job.status in {"QUEUED", "RUNNING"}:
        raise HTTPException(status_code=409, detail="解析任务正在进行中")
    if not force and job is not None and job.status == "FAILED" and job.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=409, detail="自动重试次数已达上限，请使用重新解析")
    job = enqueue_parse(db, version, user.id, force=force)
    if document.current_version == version.version_no and job.status == "QUEUED":
        document.status = "UPLOADED"
    db.add(
        AuditLog(
            user_id=user.id,
            action="reparse_document_version" if force else "request_document_parse",
            resource_type="document",
            resource_id=document.id,
            detail=f"version={version_no}",
        )
    )
    db.commit()
    db.refresh(job)
    return job


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions/{version_no}/parsed",
    response_model=DocumentParseResultOutput,
)
def get_parsed_document(
    kb_id: UUID,
    doc_id: UUID,
    version_no: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    document = visible_document(db, str(kb_id), str(doc_id), user)
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == version_no,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    result = db.get(DocumentParseResult, version.id)
    if result is None:
        raise HTTPException(status_code=404, detail="解析结果尚未生成")
    return result


@app.get(
    "/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/provenance",
    response_model=ProvenanceOutput,
)
def document_provenance(
    kb_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Return bounded, evidence-backed document/version/chunk relationships."""
    document = visible_document(db, str(kb_id), str(doc_id), user)
    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_no.desc())
        .limit(9)
    ).all()
    items = []
    for version in versions[:8]:
        result = db.get(DocumentParseResult, version.id)
        chunks = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version.id)
            .order_by(DocumentChunk.ordinal)
            .limit(6)
        ).all()
        chunk_count = (
            db.scalar(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_version_id == version.id
                )
            )
            or 0
        )
        items.append(
            ProvenanceVersion(
                version_no=version.version_no,
                status=version.status,
                parser_name=result.parser_name if result else None,
                page_count=len(result.structure.get("pages", [])) if result else 0,
                chunk_count=chunk_count,
                chunks=[
                    ProvenanceChunk(
                        id=chunk.id,
                        ordinal=chunk.ordinal,
                        page_no=chunk.page_no,
                        block_type=chunk.block_type,
                        excerpt=chunk.content[:320],
                    )
                    for chunk in chunks
                ],
            )
        )
    return ProvenanceOutput(
        document_id=document.id,
        title=document.title,
        current_version=document.current_version,
        versions=items,
        more_versions=len(versions) > 8,
    )


@app.get("/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions/{version_no}/preview")
def preview_document_version(
    kb_id: UUID,
    doc_id: UUID,
    version_no: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    document = visible_document(db, str(kb_id), str(doc_id), user)
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == version_no,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    extension = Path(version.original_filename).suffix.lower()
    if extension not in {".md", ".html", ".htm"}:
        raise HTTPException(status_code=415, detail="此格式尚不支持文本预览")
    if version.size_bytes > 1024 * 1024:
        raise HTTPException(status_code=413, detail="预览文件超过 1 MiB")
    path = blob_path(version.sha256)
    if not path.is_file():
        raise HTTPException(status_code=503, detail="原文件暂不可用")
    return {"format": extension.lstrip("."), "content": path.read_text(encoding="utf-8")}


@app.get("/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/versions/{version_no}/download")
def download_document_version(
    kb_id: UUID,
    doc_id: UUID,
    version_no: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    document = visible_document(db, str(kb_id), str(doc_id), user)
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_no == version_no,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    path = blob_path(version.sha256)
    if not path.is_file():
        raise HTTPException(status_code=503, detail="原文件暂不可用")
    db.add(
        AuditLog(
            user_id=user.id,
            action="download_document_version",
            resource_type="document",
            resource_id=document.id,
        )
    )
    db.commit()
    return FileResponse(
        path, media_type="application/octet-stream", filename=version.original_filename
    )


@app.get("/api/v1/knowledge-bases", response_model=list[KnowledgeBaseOutput])
def list_knowledge_bases(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(KnowledgeBase).where(KnowledgeBase.status == "ACTIVE")
    if user.role != "ADMIN":
        stmt = (
            stmt.outerjoin(
                KnowledgeBaseMember,
                KnowledgeBaseMember.knowledge_base_id == KnowledgeBase.id,
            )
            .where(or_(KnowledgeBase.owner_id == user.id, KnowledgeBaseMember.user_id == user.id))
            .distinct()
        )
    return db.scalars(stmt.order_by(KnowledgeBase.created_at.desc())).all()


@app.post("/api/v1/knowledge-bases", response_model=KnowledgeBaseOutput, status_code=201)
def create_knowledge_base(
    data: KnowledgeBaseCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    kb = KnowledgeBase(name=data.name.strip(), description=data.description, owner_id=user.id)
    db.add(kb)
    db.flush()
    db.add(
        AuditLog(
            user_id=user.id, action="create_kb", resource_type="knowledge_base", resource_id=kb.id
        )
    )
    db.commit()
    db.refresh(kb)
    return kb


@app.get("/api/v1/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOutput)
def get_knowledge_base(
    kb_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return visible_kb(db, str(kb_id), user)


@app.patch("/api/v1/knowledge-bases/{kb_id}/cloud-access", response_model=KnowledgeBaseOutput)
def update_cloud_access(
    kb_id: UUID,
    data: CloudAccessUpdate,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    kb = visible_kb(db, str(kb_id), admin)
    kb.cloud_enabled = data.enabled
    db.add(
        AuditLog(
            user_id=admin.id,
            action="enable_cloud_access" if data.enabled else "disable_cloud_access",
            resource_type="knowledge_base",
            resource_id=kb.id,
        )
    )
    db.commit()
    db.refresh(kb)
    return kb


@app.post("/api/v1/knowledge-bases/{kb_id}/members", status_code=201)
def add_member(
    kb_id: UUID,
    data: MemberCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    kb = visible_kb(db, str(kb_id), user)
    if user.role != "ADMIN" and kb.owner_id != user.id:
        raise HTTPException(status_code=403, detail="需要知识库拥有者权限")
    try:
        member_id = str(UUID(data.user_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="无效的用户 ID") from None
    if db.get(User, member_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    member = KnowledgeBaseMember(
        knowledge_base_id=kb.id, user_id=member_id, member_role=data.member_role
    )
    db.add(member)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="成员已存在") from None
    db.add(
        AuditLog(
            user_id=user.id, action="add_member", resource_type="knowledge_base", resource_id=kb.id
        )
    )
    db.commit()
    return {"id": member.id, "user_id": member.user_id, "member_role": member.member_role}
