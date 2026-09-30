"""Graph evidence must improve recall without escaping source and version boundaries."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import add_user, login
from pydantic import SecretStr
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.evaluate_retrieval import evaluate
from knowledge_api.graph_evidence_audit import audit
from knowledge_api.main import retrieval_terms
from knowledge_api.model_gateway import ModelAnswer
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
    KnowledgeBase,
)
from knowledge_api.retrieval import retrieve_chunks


def _document(db, kb_id, user_id, title, content, updated_at):
    document = Document(
        knowledge_base_id=kb_id,
        title=title,
        file_type="md",
        status="PARSED",
        created_by=user_id,
        updated_at=updated_at,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        original_filename=title + ".md",
        content_type="text/markdown",
        sha256="0" * 64,
        size_bytes=len(content),
        status="PARSED",
        created_by=user_id,
    )
    db.add(version)
    db.flush()
    chunk = DocumentChunk(
        document_version_id=version.id,
        ordinal=0,
        block_type="paragraph",
        content=content,
    )
    db.add(chunk)
    db.flush()
    return document, version, chunk


def test_graph_evidence_recall_citation_and_ablation(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_model", None)
    monkeypatch.setattr(settings, "rerank_base_url", None)
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(1))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(1))
    user_id = add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post(
        "/api/v1/knowledge-bases", headers=headers, json={"name": "Graph retrieval"}
    ).json()["id"]
    other_kb_id = http.post(
        "/api/v1/knowledge-bases", headers=headers, json={"name": "Other"}
    ).json()["id"]
    quote = "Alpha Company builds Beta Product for customers."
    question = "Alpha Company 的关系是什么"
    with Session(engine) as db:
        db.get(KnowledgeBase, kb_id).cloud_enabled = True
        target, version, chunk = _document(
            db, kb_id, user_id, "target", quote, datetime(2020, 1, 1, tzinfo=UTC)
        )
        source_entity = GraphEntity(
            knowledge_base_id=kb_id,
            name="Alpha Company",
            normalized_name="alpha company",
            entity_type="组织",
        )
        target_entity = GraphEntity(
            knowledge_base_id=kb_id,
            name="Beta Product",
            normalized_name="beta product",
            entity_type="产品",
        )
        db.add_all([source_entity, target_entity])
        db.flush()
        evidence = GraphEvidence(
            knowledge_base_id=kb_id,
            source_entity_id=source_entity.id,
            target_entity_id=target_entity.id,
            relation_type="builds",
            document_version_id=version.id,
            chunk_id=chunk.id,
            quote=quote,
            confidence=0.75,
            model="test",
        )
        db.add(evidence)
        db.add(
            GraphEvidence(
                knowledge_base_id=kb_id,
                source_entity_id=source_entity.id,
                target_entity_id=target_entity.id,
                relation_type="unsupported",
                document_version_id=version.id,
                chunk_id=chunk.id,
                quote="Alpha Company owns Gamma Product, according to a forged quote.",
                confidence=0.75,
                model="test",
            )
        )
        for index in range(15):
            _document(
                db,
                kb_id,
                user_id,
                f"decoy-{index}",
                f"Alpha Company background note {index}.",
                datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            )
        other, other_version, other_chunk = _document(
            db,
            other_kb_id,
            user_id,
            "private",
            "Alpha Company private relationship.",
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        other_entity = GraphEntity(
            knowledge_base_id=other_kb_id,
            name="Alpha Company",
            normalized_name="alpha company",
            entity_type="组织",
        )
        other_target = GraphEntity(
            knowledge_base_id=other_kb_id,
            name="Private Partner",
            normalized_name="private partner",
            entity_type="组织",
        )
        db.add_all([other_entity, other_target])
        db.flush()
        db.add(
            GraphEvidence(
                knowledge_base_id=other_kb_id,
                source_entity_id=other_entity.id,
                target_entity_id=other_target.id,
                relation_type="private",
                document_version_id=other_version.id,
                chunk_id=other_chunk.id,
                quote=other_chunk.content,
                confidence=0.75,
                model="test",
            )
        )
        db.commit()
        evidence_id = evidence.id
        target_id = target.id
        chunk_id = chunk.id
        other_id = other.id

    with Session(engine) as db:
        terms = retrieval_terms(question)
        baseline = retrieve_chunks(db, kb_id, question, terms, [], limit=10, include_graph=False)
        trace = {}
        combined = retrieve_chunks(db, kb_id, question, terms, [], limit=10, trace=trace)
        assert target_id not in {row[0].id for row in baseline}
        assert target_id in {row[0].id for row in combined}
        assert other_id not in {row[0].id for row in combined}
        assert [item["id"] for item in trace[chunk_id]["graph_evidence"]] == [evidence_id]
        dataset = tmp_path / "graph-retrieval.jsonl"
        dataset.write_text(
            json.dumps(
                {
                    "question": question,
                    "expected_document_ids": [target_id],
                    "expected_chunk_ids": [chunk_id],
                    "expected_graph_evidence_ids": [evidence_id],
                    "tags": ["relation"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        report = evaluate(db, kb_id, dataset)
    assert report["baseline"]["recall_at_10"] == 0.0
    assert report["with_graph"]["recall_at_10"] == 1.0
    assert report["baseline"]["chunk_recall_at_10"] == 0.0
    assert report["with_graph"]["chunk_recall_at_10"] == 1.0
    assert report["with_graph"]["document_recall_at_10"] == 1.0
    diagnostic = report["case_diagnostics"][0]["with_graph"]
    assert diagnostic["expected_chunk_ranks"]["graph"] == 1
    assert diagnostic["chunk_outcome"] == "hit"
    assert report["with_graph"]["graph_evidence_hit_rate"] == 1.0
    assert report["graph_new_hits"] == 1
    assert report["by_tag"]["relation"]["with_graph"]["recall_at_10"] == 1.0

    seen = {}

    def answer(_settings, _question, sources, history=None):
        seen["sources"] = sources
        return ModelAnswer("Alpha Company builds Beta Product [1]", 20, 10)

    monkeypatch.setattr("knowledge_api.main.answer_with_sources", answer)
    response = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/ask", headers=headers, json={"question": question}
    )
    assert response.status_code == 200
    assert "从本片段抽取的关系线索" in seen["sources"][0].content
    citation = response.json()["citations"][0]
    assert citation["document_id"] == target_id
    assert citation["version_no"] == 1
    assert citation["chunk_id"] == chunk_id
    assert citation["graph_evidence"][0]["quote"] == quote
    assert "unsupported" not in json.dumps(citation)
    source = http.get(
        f"/api/v1/knowledge-bases/{kb_id}/citations/{chunk_id}", headers=headers
    ).json()
    assert source["content"] == quote
    graph = http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=headers).json()
    assert "unsupported" not in {edge["relation"] for edge in graph["edges"]}

    def stream(_settings, _question, sources, history):
        yield "delta", "Alpha Company builds Beta Product [1]"
        yield "usage", (20, 10)

    monkeypatch.setattr("knowledge_api.main.stream_answer_with_sources", stream)
    session_id = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/chat-sessions", headers=headers, json={"title": "新对话"}
    ).json()["id"]
    streamed = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages/stream",
        headers=headers,
        json={"question": question},
    )
    assert streamed.status_code == 200
    done = next(
        json.loads(line.removeprefix("data: "))
        for line in streamed.text.splitlines()
        if line.startswith("data: ") and '"graph_evidence"' in line
    )
    assert done["citations"][0]["graph_evidence"][0]["id"] == evidence_id
    saved = http.get(
        f"/api/v1/knowledge-bases/{kb_id}/chat-sessions/{session_id}/messages", headers=headers
    ).json()
    assert saved[-1]["citations"][0]["graph_evidence"][0]["id"] == evidence_id

    with Session(engine) as db:
        document = db.get(Document, target_id)
        document.current_version = 2
        new_version = DocumentVersion(
            document_id=target_id,
            version_no=2,
            original_filename="new.md",
            content_type="text/markdown",
            sha256="1" * 64,
            size_bytes=15,
            status="PARSED",
            created_by=user_id,
        )
        db.add(new_version)
        db.flush()
        db.add(
            DocumentChunk(
                document_version_id=new_version.id,
                ordinal=0,
                block_type="paragraph",
                content="New unrelated policy.",
            )
        )
        db.commit()
        trace = {}
        current = retrieve_chunks(db, kb_id, question, retrieval_terms(question), [], trace=trace)
        assert target_id not in {row[0].id for row in current}
        assert all(not item["graph_evidence"] for item in trace.values())


def test_signature_listing_is_not_a_graph_retrieval_clue(client, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "embedding_model", None)
    monkeypatch.setattr(get_settings(), "rerank_base_url", None)
    user_id = add_user(engine, "signature-owner")
    headers = login(http, "signature-owner")
    kb_id = http.post(
        "/api/v1/knowledge-bases", headers=headers, json={"name": "Signature evidence"}
    ).json()["id"]
    quote = "国家电网有限公司客户服务中心 大数据服务部 刘鲲鹏 2021-06-09"
    with Session(engine) as db:
        _document_record, version, chunk = _document(
            db, kb_id, user_id, "signature", quote, datetime(2020, 1, 1, tzinfo=UTC)
        )
        person = GraphEntity(
            knowledge_base_id=kb_id, name="刘鲲鹏", normalized_name="刘鲲鹏", entity_type="人物"
        )
        department = GraphEntity(
            knowledge_base_id=kb_id,
            name="大数据服务部",
            normalized_name="大数据服务部",
            entity_type="组织",
        )
        db.add_all([person, department])
        db.flush()
        db.add(
            GraphEvidence(
                knowledge_base_id=kb_id,
                source_entity_id=person.id,
                target_entity_id=department.id,
                relation_type="所属部门",
                document_version_id=version.id,
                chunk_id=chunk.id,
                quote=quote,
                confidence=0.75,
                model="test",
            )
        )
        db.commit()
        assert audit(db, kb_id)["flagged_count"] == 1
        trace = {}
        diagnostics = {}
        retrieve_chunks(
            db,
            kb_id,
            "刘鲲鹏属于哪个部门？",
            retrieval_terms("刘鲲鹏属于哪个部门？"),
            [],
            trace=trace,
            diagnostics=diagnostics,
        )
        assert diagnostics["graph"] == []
        assert diagnostics["graph_details"]["filtered_evidence"] == 1
        assert all(not item["graph_evidence"] for item in trace.values())
        graph = http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=headers).json()
        assert graph["edges"] == []
