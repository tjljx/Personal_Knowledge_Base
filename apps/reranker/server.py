"""Small local cross-encoder service for ranking a bounded candidate set."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastembed.rerank.cross_encoder import TextCrossEncoder
from pydantic import BaseModel, Field

model: TextCrossEncoder | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global model
    model = TextCrossEncoder(
        model_name=os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base"),
        cache_dir=os.environ.get("RERANK_CACHE_DIR", "/models"),
        threads=int(os.environ.get("RERANK_THREADS", "4")),
    )
    yield
    model = None


app = FastAPI(lifespan=lifespan)


class RerankInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    documents: list[str] = Field(min_length=1, max_length=20)


@app.get("/health")
def health() -> dict:
    return {"ready": model is not None}


@app.post("/rerank")
def rerank(data: RerankInput) -> dict:
    if model is None:
        raise RuntimeError("Reranker is still loading")
    scores = model.rerank(data.query, [text[:4000] for text in data.documents])
    return {"scores": [float(score) for score in scores]}
