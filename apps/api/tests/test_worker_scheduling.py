"""Large embedding backlogs must not starve graph extraction."""

from sqlalchemy.orm import Session

from knowledge_api import worker


def test_background_graph_and_embedding_work_alternate(client, monkeypatch):
    _, engine = client
    calls = []
    monkeypatch.setattr(worker, "_prefer_graph_next", True)
    monkeypatch.setattr(worker, "reconcile_stale_calls", lambda *_: False)
    monkeypatch.setattr(worker, "backfill_next_chunks", lambda *_: False)
    monkeypatch.setattr(worker, "backfill_next_semantic", lambda *_: False)
    monkeypatch.setattr(worker, "backfill_next_graph", lambda *_: calls.append("graph") or True)
    monkeypatch.setattr(
        worker, "backfill_next_embeddings", lambda *_: calls.append("embedding") or True
    )

    with Session(engine) as db:
        assert worker.process_next(db)
        assert worker.process_next(db)

    assert calls == ["graph", "embedding"]
