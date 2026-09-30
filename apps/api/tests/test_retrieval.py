"""Semantic retrieval must respect knowledge-base and current-version boundaries."""

import json
from decimal import Decimal

from conftest import add_user, login
from pydantic import SecretStr
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.embeddings import EmbeddingError
from knowledge_api.evaluate_retrieval import evaluate
from knowledge_api.model_gateway import ModelAnswer
from knowledge_api.models import KnowledgeBase
from knowledge_api.worker import process_next


def test_semantic_recall_and_no_evidence_refusal(client, tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge_api.worker.backfill_next_graph", lambda _db: False)
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "embedding_model", "test-local-embedding")
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(100))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(100))

    def vectors(_settings, texts):
        return [
            [1.0, 0.0, 0.0]
            if "报销" in text or "费用" in text
            else [0.0, 0.0, 1.0]
            if "月球" in text
            else [0.0, 1.0, 0.0]
            for text in texts
        ]

    monkeypatch.setattr("knowledge_api.worker.embed_texts", vectors)
    monkeypatch.setattr(
        "knowledge_api.retrieval.embed_query",
        lambda settings, question: vectors(settings, [question])[0],
    )
    seen = {}

    def answer(_settings, _question, sources, history=None):
        seen["sources"] = sources
        return ModelAnswer("依据资料回答 [1]", 20, 10)

    monkeypatch.setattr("knowledge_api.main.answer_with_sources", answer)
    add_user(engine, "owner")
    add_user(engine, "outsider")
    owner = login(http, "owner")
    outsider = login(http, "outsider")
    kb = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "费用"}).json()
    with Session(engine) as db:
        db.get(KnowledgeBase, kb["id"]).cloud_enabled = True
        db.commit()
    other_kb = http.post("/api/v1/knowledge-bases", headers=outsider, json={"name": "其他"}).json()
    endpoint = f"/api/v1/knowledge-bases/{kb['id']}"
    document = http.post(
        f"{endpoint}/documents",
        headers=owner,
        files={"file": ("rules.md", "# 财务制度\n\n差旅报销需要发票。".encode())},
    ).json()
    http.post(
        f"/api/v1/knowledge-bases/{other_kb['id']}/documents",
        headers=outsider,
        files={"file": ("private.md", "# Private\n\n费用审批秘密。".encode())},
    )
    with Session(engine) as db:
        assert process_next(db)
        assert process_next(db)
        assert process_next(db)
        assert not process_next(db)
    reply = http.post(f"{endpoint}/ask", headers=owner, json={"question": "费用怎么处理"})
    assert reply.status_code == 200
    assert reply.json()["citations"][0]["document_id"] == document["id"]
    assert "差旅报销" in seen["sources"][0].content
    assert all("秘密" not in source.content for source in seen["sources"])

    def unavailable(_settings, _question):
        raise EmbeddingError("model unavailable")

    monkeypatch.setattr("knowledge_api.retrieval.embed_query", unavailable)
    fallback = http.post(f"{endpoint}/ask", headers=owner, json={"question": "差旅报销需要发票吗"})
    assert fallback.status_code == 200
    assert fallback.json()["citations"][0]["document_id"] == document["id"]
    monkeypatch.setattr(
        "knowledge_api.retrieval.embed_query",
        lambda settings, question: vectors(settings, [question])[0],
    )
    assert (
        http.post(
            f"{endpoint}/ask", headers=outsider, json={"question": "费用怎么处理"}
        ).status_code
        == 404
    )
    dataset = tmp_path / "retrieval.jsonl"
    dataset.write_text(
        "\n".join(
            json.dumps(case, ensure_ascii=False)
            for case in [
                {"question": "费用怎么处理", "expected_document_ids": [document["id"]]},
                {"question": "月球基地位置", "should_refuse": True},
            ]
        ),
        encoding="utf-8",
    )
    with Session(engine) as db:
        metrics = evaluate(db, kb["id"], dataset)
    assert metrics["recall_at_10"] == 1.0
    assert metrics["empty_retrieval_rate"] == 1.0

    # Updating the document must not expose the old version through vector search.
    http.post(
        f"{endpoint}/documents/{document['id']}/versions",
        headers=owner,
        files={"file": ("rules.md", "# 新制度\n\n采购流程另见附件。".encode())},
    )
    with Session(engine) as db:
        assert process_next(db)
        assert process_next(db)
    seen.clear()
    no_evidence = http.post(f"{endpoint}/ask", headers=owner, json={"question": "费用怎么处理"})
    assert no_evidence.status_code == 200
    assert no_evidence.json()["citations"] == []
    assert not seen
