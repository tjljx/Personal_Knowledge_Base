"""Qdrant projection of current chunk embeddings; PostgreSQL remains source of truth."""

import httpx

from knowledge_api.config import Settings


class VectorStoreError(Exception):
    pass


def _endpoint(settings: Settings) -> str:
    if not settings.vector_store_url:
        raise VectorStoreError("Vector store is not configured")
    return f"{settings.vector_store_url.rstrip('/')}/collections/{settings.vector_store_collection}"


def ensure_collection(settings: Settings, dimensions: int) -> None:
    endpoint = _endpoint(settings)
    try:
        response = httpx.get(endpoint, timeout=10)
        if response.status_code == 404:
            response = httpx.put(
                endpoint,
                json={"vectors": {"size": dimensions, "distance": "Cosine"}},
                timeout=20,
            )
            response.raise_for_status()
            for field in ("knowledge_base_id", "model"):
                response = httpx.put(
                    f"{endpoint}/index",
                    json={"field_name": field, "field_schema": "keyword"},
                    timeout=20,
                )
                response.raise_for_status()
            return
        response.raise_for_status()
        configured = response.json()["result"]["config"]["params"]["vectors"]["size"]
        if configured != dimensions:
            raise VectorStoreError("Qdrant collection dimensions do not match embedding model")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise VectorStoreError("Qdrant collection unavailable") from exc


def upsert_vectors(settings: Settings, points: list[dict]) -> None:
    if not points:
        return
    ensure_collection(settings, len(points[0]["vector"]))
    try:
        response = httpx.put(
            f"{_endpoint(settings)}/points",
            params={"wait": "true"},
            json={"points": points},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise VectorStoreError("Qdrant upsert failed") from exc


def delete_vectors(settings: Settings, ids: list[str]) -> None:
    if not ids:
        return
    try:
        response = httpx.request(
            "POST",
            f"{_endpoint(settings)}/points/delete",
            params={"wait": "true"},
            json={"points": ids},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise VectorStoreError("Qdrant deletion failed") from exc


def query_vectors(
    settings: Settings, knowledge_base_id: str, vector: list[float], limit: int = 100
) -> list[tuple[str, float]]:
    try:
        response = httpx.post(
            f"{_endpoint(settings)}/points/query",
            json={
                "query": vector,
                "filter": {
                    "must": [
                        {"key": "knowledge_base_id", "match": {"value": knowledge_base_id}},
                        {"key": "model", "match": {"value": settings.embedding_model}},
                    ]
                },
                "score_threshold": settings.embedding_min_similarity,
                "limit": limit,
                "with_payload": False,
            },
            timeout=10,
        )
        response.raise_for_status()
        return [
            (str(item["id"]), float(item["score"])) for item in response.json()["result"]["points"]
        ]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise VectorStoreError("Qdrant query failed") from exc


def present_ids(settings: Settings, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    present = set()
    try:
        for start in range(0, len(ids), 100):
            response = httpx.post(
                f"{_endpoint(settings)}/points",
                json={"ids": ids[start : start + 100], "with_payload": False, "with_vector": False},
                timeout=15,
            )
            response.raise_for_status()
            present.update(str(item["id"]) for item in response.json()["result"])
        return present
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise VectorStoreError("Qdrant coverage check failed") from exc
