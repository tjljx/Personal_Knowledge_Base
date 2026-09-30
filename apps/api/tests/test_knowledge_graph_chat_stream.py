"""Real session persistence and evidence backed graph aggregation."""

import json
from decimal import Decimal

from conftest import add_user, login
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.knowledge_graph import parse_relations
from knowledge_api.model_gateway import ModelAnswer
from knowledge_api.models import (
    DocumentChunk,
    GraphChunkExtraction,
    GraphEntity,
    GraphEvidence,
    KnowledgeBase,
)
from knowledge_api.worker import process_next


def test_graph_rejects_claim_without_exact_quote():
    source = "Alpha Company builds Beta Product for customers."
    rows = [
        {
            "source": "Alpha Company",
            "target": "Beta Product",
            "relation": "builds",
            "quote": source,
        },
        {
            "source": "Alpha Company",
            "target": "Gamma Product",
            "relation": "builds",
            "quote": source,
        },
    ]
    result = parse_relations(json.dumps({"relations": rows}), source)
    assert len(result) == 1
    assert result[0]["target"] == "Beta Product"


def test_graph_rejects_signature_listing_without_predicate():
    quote = "国家电网有限公司客户服务中心 大数据服务部 刘鲲鹏 2021-06-09"
    rows = [
        {
            "source": "刘鲲鹏",
            "source_type": "人物",
            "target": "大数据服务部",
            "target_type": "组织",
            "relation": "所属部门",
            "quote": quote,
        }
    ]
    assert parse_relations(json.dumps({"relations": rows}, ensure_ascii=False), quote) == []
    supported = "刘鲲鹏担任大数据服务部负责人。"
    rows[0]["quote"] = supported
    rows[0]["relation"] = "担任负责人"
    assert len(parse_relations(json.dumps({"relations": rows}, ensure_ascii=False), supported)) == 1


def test_graph_progress_counts_current_chunks_and_respects_access(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    add_user(engine, "outsider")
    owner = login(http, "owner")
    outsider = login(http, "outsider")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "Progress"}).json()[
        "id"
    ]
    path = f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph/progress"
    response = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=owner,
        files={"file": ("progress.md", b"One source fact for graph extraction.")},
    )
    assert response.status_code == 201
    assert http.get(path, headers=outsider).status_code == 404
    assert http.get(path, headers=owner).json()["total_chunks"] == 0

    with Session(engine) as db:
        assert process_next(db)
    pending = http.get(path, headers=owner).json()
    assert pending["total_documents"] == pending["parsed_documents"] == 1
    assert pending["total_chunks"] == pending["remaining_chunks"] == 1
    assert pending["processed_chunks"] == 0

    with Session(engine) as db:
        chunk = db.scalar(select(DocumentChunk))
        db.add(GraphChunkExtraction(chunk_id=chunk.id, status="SUCCEEDED"))
        db.commit()
    completed = http.get(path, headers=owner).json()
    assert completed["processed_chunks"] == 1
    assert completed["remaining_chunks"] == 0


def test_cross_document_graph_merges_entities_and_keeps_evidence(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(1))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(1))
    add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=headers, json={"name": "Graph"}).json()[
        "id"
    ]
    with Session(engine) as db:
        db.get(KnowledgeBase, kb_id).cloud_enabled = True
        db.commit()
    for name, content in [
        ("first.md", "Alpha Company builds Beta Product for customers."),
        ("second.md", "Alpha Company sells Beta Product worldwide."),
    ]:
        response = http.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            headers=headers,
            files={"file": (name, content.encode())},
        )
        assert response.status_code == 201

    def extract(_settings, content):
        relation = "builds" if "builds" in content else "sells"
        return ModelAnswer(
            json.dumps(
                {
                    "relations": [
                        {
                            "source": "Alpha Company",
                            "source_type": "组织",
                            "target": "Beta Product",
                            "target_type": "产品",
                            "relation": relation,
                            "quote": content.strip(),
                        }
                    ]
                }
            ),
            30,
            20,
        )

    monkeypatch.setattr("knowledge_api.knowledge_graph._extract", extract)
    with Session(engine) as db:
        for _ in range(8):
            if not process_next(db):
                break
    response = http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert len(graph["nodes"]) == 2
    assert {edge["relation"] for edge in graph["edges"]} == {"builds", "sells"}
    assert {e["document_title"] for edge in graph["edges"] for e in edge["evidence"]} == {
        "first.md",
        "second.md",
    }
    with Session(engine) as db:
        assert len(db.scalars(select(GraphEvidence)).all()) == 2

        source, target = db.scalars(select(GraphEntity)).all()
        first_evidence = db.scalar(select(GraphEvidence))
        for index in range(501):
            db.add(
                GraphEvidence(
                    knowledge_base_id=kb_id,
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type=f"related-{index}",
                    document_version_id=first_evidence.document_version_id,
                    chunk_id=first_evidence.chunk_id,
                    quote="Alpha Company builds Beta Product for customers.",
                    confidence=0.75,
                    model="test-model",
                )
            )
        db.commit()
    full_graph = http.get(
        f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=headers
    ).json()
    assert len(full_graph["edges"]) == 503
    assert sum(len(edge["evidence"]) for edge in full_graph["edges"]) == 503


def test_streaming_chat_keeps_followup_history(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(1))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(1))
    add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=headers, json={"name": "Chat"}).json()[
        "id"
    ]
    with Session(engine) as db:
        db.get(KnowledgeBase, kb_id).cloud_enabled = True
        db.commit()
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=headers,
        files={"file": ("memo.md", b"Alpha Company builds Beta Product for customers.")},
    )
    with Session(engine) as db:
        assert process_next(db)
    session_id = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/chat-sessions", headers=headers, json={"title": "新对话"}
    ).json()["id"]
    seen = []

    def stream(_settings, question, sources, history):
        seen.append((question, list(history)))
        yield "delta", "Alpha Company "
        yield "delta", "builds Beta Product [1]"
        yield "usage", (30, 20)

    monkeypatch.setattr("knowledge_api.main.stream_answer_with_sources", stream)
    endpoint = f"/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages/stream"
    for question in ("Alpha Company", "What does Alpha Company build?"):
        response = http.post(endpoint, headers=headers, json={"question": question})
        assert response.status_code == 200
        assert "event: delta" in response.text
        assert "event: done" in response.text
    assert seen[1][1][-2:] == [
        {"role": "user", "content": "Alpha Company"},
        {"role": "assistant", "content": "Alpha Company builds Beta Product [1]"},
    ]
    messages = http.get(
        f"/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages", headers=headers
    ).json()
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
