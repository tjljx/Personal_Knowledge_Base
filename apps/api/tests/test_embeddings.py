"""The configured local model must return usable vectors for indexing and queries."""

import httpx
import pytest

from knowledge_api.config import get_settings
from knowledge_api.embeddings import EmbeddingError, embed_query, embed_texts


def test_query_instruction_and_vector_shape(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "embedding_model": "qwen3-embedding:0.6b",
            "embedding_expected_dimensions": 3,
            "embedding_query_instruction": "Find relevant evidence.",
        }
    )
    requests = []

    def fake_post(url, *, json, timeout):
        requests.append((url, json, timeout))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert embed_query(settings, "费用如何审批？") == [0.1, 0.2, 0.3]
    assert requests[0][1]["input"] == ["Instruct: Find relevant evidence.\nQuery: 费用如何审批？"]
    assert requests[0][1]["truncate"] is False
    assert requests[0][2] == settings.embedding_query_timeout_seconds


def test_rejects_wrong_vector_dimensions(monkeypatch):
    settings = get_settings().model_copy(
        update={"embedding_model": "qwen3-embedding:0.6b", "embedding_expected_dimensions": 3}
    )

    def fake_post(url, *, json, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(EmbeddingError):
        embed_texts(settings, ["sample"])
