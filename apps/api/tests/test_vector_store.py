"""Qdrant requests must use a dedicated collection and knowledge-base filter."""

import httpx

from knowledge_api.config import get_settings
from knowledge_api.vector_store import present_ids, query_vectors, upsert_vectors


def test_qdrant_index_write_query_and_coverage(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "vector_store_url": "http://qdrant:6333",
            "vector_store_collection": "test_chunks",
            "embedding_model": "test-model",
        }
    )
    requests = []

    def response(method, url, *, json=None, params=None, timeout=None):
        requests.append((method, url, json, params))
        result = {"result": True}
        if method == "GET":
            return httpx.Response(404, request=httpx.Request(method, url))
        if method == "POST" and url.endswith("/points/query"):
            result = {"result": {"points": [{"id": "chunk-id", "score": 0.9}]}}
        elif method == "POST" and url.endswith("/points"):
            result = {"result": [{"id": "chunk-id"}]}
        return httpx.Response(200, json=result, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response("GET", url, **kwargs))
    monkeypatch.setattr(httpx, "put", lambda url, **kwargs: response("PUT", url, **kwargs))
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: response("POST", url, **kwargs))
    upsert_vectors(settings, [{"id": "chunk-id", "vector": [1.0, 0.0], "payload": {}}])
    assert query_vectors(settings, "kb-id", [1.0, 0.0]) == [("chunk-id", 0.9)]
    assert present_ids(settings, ["chunk-id"]) == {"chunk-id"}
    query = next(body for method, url, body, _ in requests if url.endswith("/points/query"))
    assert query["filter"]["must"] == [
        {"key": "knowledge_base_id", "match": {"value": "kb-id"}},
        {"key": "model", "match": {"value": "test-model"}},
    ]
