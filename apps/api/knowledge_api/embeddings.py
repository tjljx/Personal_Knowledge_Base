"""Local Ollama embedding boundary; vectors are a rebuildable search projection."""

import math

import httpx

from knowledge_api.config import Settings


class EmbeddingError(Exception):
    pass


def embed_texts(
    settings: Settings, texts: list[str], *, timeout_seconds: int | None = None
) -> list[list[float]]:
    if not settings.embedding_model:
        raise EmbeddingError("未配置本地向量模型")
    if not texts:
        return []
    try:
        response = httpx.post(
            f"{settings.embedding_base_url.rstrip('/')}/api/embed",
            json={"model": settings.embedding_model, "input": texts, "truncate": False},
            timeout=timeout_seconds or settings.embedding_timeout_seconds,
        )
        response.raise_for_status()
        vectors = response.json()["embeddings"]
        if not isinstance(vectors, list) or len(vectors) != len(texts) or not vectors:
            raise ValueError("embedding count mismatch")
        width = len(vectors[0]) if isinstance(vectors[0], list) else 0
        if not width or any(
            not isinstance(vector, list)
            or len(vector) != width
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise ValueError("invalid embedding vector")
        if (
            settings.embedding_expected_dimensions
            and width != settings.embedding_expected_dimensions
        ):
            raise ValueError("embedding dimensions do not match configured model")
        return vectors
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise EmbeddingError("本地向量服务暂时不可用或返回格式异常") from exc


def embed_query(settings: Settings, question: str) -> list[float]:
    instruction = settings.embedding_query_instruction.strip()
    text = f"Instruct: {instruction}\nQuery: {question}" if instruction else question
    return embed_texts(settings, [text], timeout_seconds=settings.embedding_query_timeout_seconds)[
        0
    ]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0
