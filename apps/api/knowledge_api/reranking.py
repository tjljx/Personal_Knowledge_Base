"""Local cross-encoder boundary for second-stage retrieval ranking."""

import math

import httpx

from knowledge_api.config import Settings


class RerankError(Exception):
    pass


def rerank_scores(settings: Settings, question: str, passages: list[str]) -> list[float]:
    if not settings.rerank_base_url:
        raise RerankError("Reranker is not configured")
    try:
        response = httpx.post(
            f"{settings.rerank_base_url.rstrip('/')}/rerank",
            json={"query": question, "documents": passages},
            timeout=settings.rerank_timeout_seconds,
        )
        response.raise_for_status()
        scores = response.json()["scores"]
        if len(scores) != len(passages) or not all(
            isinstance(score, (float, int)) and math.isfinite(score) for score in scores
        ):
            raise ValueError("Invalid reranker scores")
        return [float(score) for score in scores]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise RerankError("Reranker unavailable or returned invalid scores") from exc
