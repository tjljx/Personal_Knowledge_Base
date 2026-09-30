"""Semantic boundaries must stay grounded in the extracted page text."""

from conftest import add_user, login
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api import semantic_chunking
from knowledge_api.models import DocumentChunk, DocumentParseResult, KnowledgeBase
from knowledge_api.worker import process_next


def _long_structure():
    return {
        "pages": [
            {
                "number": 3,
                "blocks": [
                    {"type": "heading", "text": "产品架构"},
                    {"type": "paragraph", "text": "计算节点负责运行任务。" * 70},
                    {"type": "heading", "text": "权限管理"},
                    {"type": "paragraph", "text": "管理员可以授予用户访问权限。" * 70},
                ],
            }
        ]
    }


def test_model_boundaries_group_related_text_and_preserve_page(monkeypatch):
    calls = []

    def answer(_db, _settings, _owner, _kb, prompt, _tokens, task):
        calls.append(task)
        if task == "semantic_summary":
            return {"summary": "文档介绍架构与权限。"}
        assert "全文概要：文档介绍架构与权限。" in prompt
        return {"boundaries": [71, 142]}

    monkeypatch.setattr(semantic_chunking, "_paid_json", answer)
    structure = _long_structure()
    chunks = semantic_chunking.build_semantic_chunks(None, None, "owner", "kb", structure, "指南")
    assert calls == ["semantic_summary", "semantic_chunking"]
    assert structure["chunking_method"] == "model_semantic"
    assert [c.page_no for c in chunks] == [3, 3]
    assert [c.ordinal for c in chunks] == [0, 1]
    assert "产品架构" in chunks[0].content
    assert "权限管理" in chunks[1].content
    assert all(len(c.content) <= semantic_chunking.MAX_CHUNK for c in chunks)


def test_invalid_model_boundaries_fall_back_without_losing_text(monkeypatch):
    def answer(_db, _settings, _owner, _kb, _prompt, _tokens, task):
        return {"summary": "概览"} if task == "semantic_summary" else {"boundaries": [999]}

    monkeypatch.setattr(semantic_chunking, "_paid_json", answer)
    structure = _long_structure()
    chunks = semantic_chunking.build_semantic_chunks(None, None, "owner", "kb", structure, "指南")
    assert structure["chunking_method"] == "local_semantic_fallback"
    assert "产品架构" in " ".join(c.content for c in chunks)
    assert "权限管理" in " ".join(c.content for c in chunks)
    assert all(c.page_no == 3 for c in chunks)


def test_short_document_keeps_original_source_blocks(monkeypatch):
    monkeypatch.setattr(semantic_chunking, "_paid_json", lambda *args: 1 / 0)
    structure = {
        "pages": [
            {
                "number": 1,
                "blocks": [
                    {"type": "heading", "text": "标题"},
                    {"type": "paragraph", "text": "正文"},
                ],
            }
        ]
    }
    chunks = semantic_chunking.build_semantic_chunks(None, None, "owner", "kb", structure, "指南")
    assert [c.block_type for c in chunks] == ["heading", "paragraph"]
    assert structure["chunking_method"] == "local_short_document"


def test_enabling_cloud_rebuilds_existing_long_document(client, tmp_path, monkeypatch):
    http, engine = client
    from decimal import Decimal

    from pydantic import SecretStr

    from knowledge_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(1))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(1))
    add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=headers, json={"name": "Semantic"}).json()[
        "id"
    ]
    content = (
        "# 产品架构\n\n"
        + "计算节点负责运行任务。" * 70
        + "\n\n# 权限管理\n\n"
        + "管理员可以授予用户访问权限。" * 70
    )
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=headers,
        files={"file": ("guide.md", content.encode(), "text/markdown")},
    )
    with Session(engine) as db:
        assert process_next(db)
        result = db.scalar(select(DocumentParseResult))
        assert result.structure["chunking_method"] == "local_cloud_disabled"
        old_ids = {c.id for c in db.scalars(select(DocumentChunk))}

    def answer(_db, _settings, _owner, _kb, _prompt, _tokens, task):
        return (
            {"summary": "架构与权限"} if task == "semantic_summary" else {"boundaries": [71, 142]}
        )

    monkeypatch.setattr(semantic_chunking, "_paid_json", answer)
    with Session(engine) as db:
        db.get(KnowledgeBase, kb_id).cloud_enabled = True
        db.commit()
    with Session(engine) as db:
        assert process_next(db)
        result = db.scalar(select(DocumentParseResult))
        assert result.structure["chunking_method"] == "model_semantic"
        chunks = db.scalars(select(DocumentChunk).order_by(DocumentChunk.ordinal)).all()
        assert len(chunks) == 2
        assert old_ids.isdisjoint({c.id for c in chunks})
