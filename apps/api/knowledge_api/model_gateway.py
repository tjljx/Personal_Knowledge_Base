"""Provider boundary for cloud model calls."""

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from knowledge_api.config import Settings


class ModelGatewayError(Exception):
    pass


class ModelNotConfigured(ModelGatewayError):
    pass


@dataclass(frozen=True)
class Source:
    number: int
    content: str


@dataclass(frozen=True)
class ModelAnswer:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None


def answer_with_sources(
    settings: Settings,
    question: str,
    sources: list[Source],
    history: list[dict[str, str]] | None = None,
) -> ModelAnswer:
    if settings.model_api_key is None:
        raise ModelNotConfigured("未配置模型 API 密钥")
    context = "\n\n".join(f"[{source.number}] {source.content}" for source in sources)
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "你是个人知识库助手。只能依据给出的资料回答；资料不足时明确说明。"
            "历史对话只用于理解追问，不可作为事实依据。"
            "每个事实性结论后使用本次资料的 [编号] 标注来源，不要编造引用。",
        },
    ]
    for message in (history or [])[-8:]:
        if message.get("role") in {"user", "assistant"}:
            messages.append({"role": message["role"], "content": message.get("content", "")[:2000]})
    messages.append({"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"})
    try:
        response = httpx.post(
            f"{settings.model_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}"},
            json={
                "model": settings.model_name,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": settings.model_max_output_tokens,
                **(
                    {"thinking": {"type": "disabled"}}
                    if settings.model_provider == "deepseek"
                    else {}
                ),
            },
            timeout=settings.model_timeout_seconds,
        )
    except httpx.RequestError as exc:
        raise ModelGatewayError(f"{settings.model_provider} 服务暂时不可用") from exc
    if response.is_error:
        raise ModelGatewayError(f"{settings.model_provider} 模型调用失败")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("model content is not text")
        content = content.strip()
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise ModelGatewayError(f"{settings.model_provider} 返回格式异常") from exc
    if not content:
        raise ModelGatewayError(f"{settings.model_provider} 未返回回答内容")
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or prompt_tokens < 0:
        prompt_tokens = None
    if not isinstance(completion_tokens, int) or completion_tokens < 0:
        completion_tokens = None
    return ModelAnswer(content, prompt_tokens, completion_tokens)


def stream_answer_with_sources(
    settings: Settings,
    question: str,
    sources: list[Source],
    history: list[dict[str, str]] | None = None,
) -> Iterator[tuple[str, str | tuple[int | None, int | None]]]:
    """Yield text deltas and final token usage from an OpenAI-compatible SSE stream."""
    if settings.model_api_key is None:
        raise ModelNotConfigured("未配置模型 API 密钥")
    context = "\n\n".join(f"[{source.number}] {source.content}" for source in sources)
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "你是个人知识库助手。只依据本次给出的资料回答；历史对话仅帮助理解追问，不可作为事实依据。每个事实结论用本次资料的 [编号] 标注来源；资料不足时明确拒答。",
        }
    ]
    for message in (history or [])[-8:]:
        if message.get("role") in {"user", "assistant"}:
            messages.append({"role": message["role"], "content": message.get("content", "")[:2000]})
    messages.append({"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"})
    try:
        with httpx.stream(
            "POST",
            f"{settings.model_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}"},
            json={
                "model": settings.model_name,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": settings.model_max_output_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
                **(
                    {"thinking": {"type": "disabled"}}
                    if settings.model_provider == "deepseek"
                    else {}
                ),
            },
            timeout=settings.model_timeout_seconds,
        ) as response:
            if response.is_error:
                raise ModelGatewayError(f"{settings.model_provider} 模型调用失败")
            prompt_tokens = None
            completion_tokens = None
            got_text = False
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except ValueError as exc:
                    raise ModelGatewayError("模型流式响应格式异常") from exc
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                choices = payload.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        got_text = True
                        yield "delta", content
            if not got_text:
                raise ModelGatewayError(f"{settings.model_provider} 未返回回答内容")
            yield (
                "usage",
                (
                    prompt_tokens
                    if isinstance(prompt_tokens, int) and prompt_tokens >= 0
                    else None,
                    completion_tokens
                    if isinstance(completion_tokens, int) and completion_tokens >= 0
                    else None,
                ),
            )
    except httpx.RequestError as exc:
        raise ModelGatewayError(f"{settings.model_provider} 服务暂时不可用") from exc
