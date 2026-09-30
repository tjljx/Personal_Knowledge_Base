"""Model guided, source preserving document segmentation."""

import json
import logging
import re

import httpx
from sqlalchemy.orm import Session

from knowledge_api.chunking import Chunk, _split_text, build_chunks
from knowledge_api.cloud_budget import (
    BudgetExceeded,
    BudgetNotConfigured,
    reserve_call,
    settle_call,
)
from knowledge_api.config import Settings
from knowledge_api.model_gateway import ModelAnswer

logger = logging.getLogger(__name__)
MAX_WINDOW = 4800
MAX_CHUNK = 1400
SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+(?=[A-Z])|\n+")


def _complete(settings: Settings, prompt: str, max_tokens: int) -> ModelAnswer:
    response = httpx.post(
        f"{settings.model_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}"},
        json={
            "model": settings.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
            **(
                {"thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}}
                if settings.model_provider == "deepseek"
                else {}
            ),
        },
        timeout=settings.model_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage") or {}
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise TypeError("Model response is not text")
    return ModelAnswer(content, usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _paid_json(
    db: Session,
    settings: Settings,
    owner_id: str,
    kb_id: str,
    prompt: str,
    max_tokens: int,
    task: str,
) -> dict:
    usage = reserve_call(
        db, settings, owner_id, kb_id, prompt, [], None, task=task, max_output_tokens=max_tokens
    )
    answer = None
    try:
        answer = _complete(settings, prompt, max_tokens)
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.content.strip(), flags=re.IGNORECASE)
        result = json.loads(clean)
        if not isinstance(result, dict):
            raise TypeError("Model response is not a JSON object")
        return result
    finally:
        settle_call(db, settings, usage, answer)


def _units(text: str) -> list[str]:
    """Offer sentence and paragraph boundaries without rewriting source text."""
    units = []
    for part in SENTENCE_END.split(text):
        part = " ".join(part.split())
        if not part:
            continue
        if len(part) > MAX_CHUNK:
            units.extend(_split_text(part))
        else:
            units.append(part)
    return units


def _windows(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    windows = []
    current = []
    length = 0
    for item in items:
        size = len(item[1]) + 20
        if current and length + size > MAX_WINDOW:
            windows.append(current)
            current = []
            length = 0
        current.append(item)
        length += size
    if current:
        windows.append(current)
    return windows


def _fallback(items: list[tuple[str, str]], page_no: int | None) -> list[Chunk]:
    result = []
    content = []
    block_type = "paragraph"
    for kind, text in items:
        if content and len(" ".join(content)) + len(text) + 1 > MAX_CHUNK:
            result.append(Chunk(0, page_no, block_type, " ".join(content)))
            content = []
        if not content:
            block_type = kind
        content.append(text)
    if content:
        result.append(Chunk(0, page_no, block_type, " ".join(content)))
    return result


def build_semantic_chunks(
    db: Session, settings: Settings, owner_id: str, kb_id: str, structure: dict, title: str
) -> list[Chunk]:
    """Use model boundaries for each page; keep exact extracted text and page citations.

    Invalid or unavailable model output falls back to bounded local grouping.
    """
    pages = structure.get("pages", [])
    total_chars = sum(
        len(str(block.get("text", ""))) for page in pages for block in page.get("blocks", [])
    )
    if total_chars <= MAX_CHUNK:
        structure["chunking_method"] = "local_short_document"
        return build_chunks(structure)
    overview = "\n".join(
        f"第 {page.get('number')} 页: "
        + " ".join(str(b.get("text", ""))[:350] for b in page.get("blocks", [])[:20])[:1400]
        for page in pages[:24]
    )[:12000]
    summary = ""
    if total_chars > MAX_CHUNK:
        prompt = (
            "阅读以下文档摘录，概括文章主题、章节和核心实体。只根据原文回答，"
            '不要补充事实。只返回 JSON：{"summary":"不超过400字"}。\n'
            f"标题：{title}\n{overview}"
        )
        try:
            result = _paid_json(db, settings, owner_id, kb_id, prompt, 600, "semantic_summary")
            summary = str(result.get("summary", ""))[:500]
        except (BudgetNotConfigured, BudgetExceeded):
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            logger.warning("Document overview model call failed", exc_info=True)
    structure["semantic_summary"] = summary
    chunks = []
    model_used = False
    for page in pages:
        page_no = page.get("number") if isinstance(page.get("number"), int) else None
        items = []
        for block in page.get("blocks", []):
            kind = str(block.get("type", "paragraph"))
            items.extend((kind, unit) for unit in _units(str(block.get("text", ""))))
        for window in _windows(items):
            if sum(len(text) for _, text in window) <= MAX_CHUNK or len(window) < 2:
                chunks.extend(_fallback(window, page_no))
                continue
            numbered = "\n".join(
                f"{i}. [{kind}] {text}" for i, (kind, text) in enumerate(window, 1)
            )
            prompt = (
                "你是文档语义分段器。先理解主题和论述，再选择同一话题内部连续的句子或段落组成一个检索片段。"
                "标题与其正文尽量放一起；话题转换、独立定义、不同实体关系处划界。"
                "每片约 300-1000 字，最长 1400 字。不得改写原文。"
                '只输出 JSON，例如 {"boundaries":[3,7]}，数字是每个片段最后一个单元的编号，'
                "必须包含最后一个编号。\n"
                f"文档标题：{title}\n全文概要：{summary}\n本页单元：\n{numbered}"
            )
            try:
                data = _paid_json(db, settings, owner_id, kb_id, prompt, 300, "semantic_chunking")
                boundaries = data.get("boundaries")
                if (
                    not isinstance(boundaries, list)
                    or not boundaries
                    or any(type(n) is not int for n in boundaries)
                    or boundaries != sorted(set(boundaries))
                    or boundaries[-1] != len(window)
                    or boundaries[0] < 1
                ):
                    raise ValueError("Invalid semantic boundaries")
                start = 0
                proposed = []
                for end in boundaries:
                    group = window[start:end]
                    content = " ".join(text for _, text in group)
                    if not group or len(content) > MAX_CHUNK:
                        raise ValueError("Semantic chunk exceeds limit")
                    proposed.append(Chunk(0, page_no, group[0][0], content))
                    start = end
                chunks.extend(proposed)
                model_used = True
            except (BudgetNotConfigured, BudgetExceeded):
                raise
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                logger.warning(
                    "Semantic boundary model call failed; using local boundaries", exc_info=True
                )
                chunks.extend(_fallback(window, page_no))
    structure["chunking_method"] = "model_semantic" if model_used else "local_semantic_fallback"
    return [Chunk(i, c.page_no, c.block_type, c.content) for i, c in enumerate(chunks)]
