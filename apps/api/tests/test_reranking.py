"""Cross-encoder outputs must be checked before changing citation order."""

import httpx
import pytest

from knowledge_api.config import get_settings
from knowledge_api.reranking import RerankError, rerank_scores


def test_reranker_scores(monkeypatch):
    settings = get_settings().model_copy(update={"rerank_base_url": "http://reranker:8000"})

    def post(url, *, json, timeout):
        assert json == {"query": "question", "documents": ["first", "second"]}
        return httpx.Response(200, json={"scores": [0.1, 0.9]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert rerank_scores(settings, "question", ["first", "second"]) == [0.1, 0.9]


def test_reranker_rejects_mismatched_scores(monkeypatch):
    settings = get_settings().model_copy(update={"rerank_base_url": "http://reranker:8000"})

    def post(url, *, json, timeout):
        return httpx.Response(200, json={"scores": [0.1]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RerankError):
        rerank_scores(settings, "question", ["first", "second"])
