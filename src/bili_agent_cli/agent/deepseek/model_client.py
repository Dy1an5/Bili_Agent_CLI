import json
from typing import Any

import httpx
from pydantic import ValidationError

from bili_agent_cli.agent.memory.models import MemoryExtraction
from bili_agent_cli.agent.memory.prompts import (
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    MEMORY_EXTRACTION_TOOL_NAME,
)

from .config import (
    AGENT_MAX_OUTPUT_TOKENS,
    DEEPSEEK_URL,
    DEEPSEEK_MODEL,
    load_api_key,
)
from .errors import (
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from .models import (
    ChatRequest,
    ChatResponse,
)

def build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

def build_request_body(
    request: ChatRequest,
    model: str,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": request.message,
            }
        ],
    }

def parse_provider_payload(payload: object) -> ChatResponse:
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            "供应商响应顶层不是对象"
        )

    choices = payload.get("choices")

    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError(
            "供应商响应缺少有效 choices"
        )

    first_choice = choices[0]

    if not isinstance(first_choice, dict):
        raise ProviderResponseError(
            "供应商 choices 格式错误"
        )

    message = first_choice.get("message")

    if not isinstance(message, dict):
        raise ProviderResponseError(
            "供应商响应缺少有效 message"
        )

    usage = payload.get("usage")

    if not isinstance(usage, dict):
        raise ProviderResponseError(
            "供应商响应缺少有效 usage"
        )

    local_data = {
        "answer": message.get("content"),
        "model": payload.get("model"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        },
    }

    try:
        return ChatResponse.model_validate(local_data)

    except ValidationError as exc:
        raise ProviderResponseError(
            "供应商响应无法满足本地输出契约"
        ) from exc

def parse_memory_extraction_message(message: object) -> MemoryExtraction:
    if not isinstance(message, dict):
        raise ProviderResponseError("invalid memory extraction message")

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise ProviderResponseError("missing memory extraction tool call")

    tool_call = tool_calls[0]
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    if not isinstance(function, dict):
        raise ProviderResponseError("invalid memory extraction tool call")
    if function.get("name") != MEMORY_EXTRACTION_TOOL_NAME:
        raise ProviderResponseError("unexpected memory extraction tool")

    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ProviderResponseError("invalid memory extraction arguments")

    try:
        payload = json.loads(arguments)
        return MemoryExtraction.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as error:
        raise ProviderResponseError(
            "invalid memory extraction payload"
        ) from error

async def create_memory_extraction(
    extraction_input: str,
) -> MemoryExtraction:
    request_payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": MEMORY_EXTRACTION_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": extraction_input,
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": MEMORY_EXTRACTION_TOOL_NAME,
                    "description": "提交长期记忆候选",
                    "parameters": MemoryExtraction.model_json_schema(),
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": MEMORY_EXTRACTION_TOOL_NAME},
        },
        "thinking": {"type": "disabled"},
        "max_tokens": 2_000,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                DEEPSEEK_URL,
                json=request_payload,
                headers=build_headers(load_api_key()),
            )
    except httpx.TimeoutException as error:
        raise ProviderTimeoutError("memory extraction timeout") from error
    except httpx.RequestError as error:
        raise ProviderNetworkError("memory extraction network error") from error

    if not response.is_success:
        raise ProviderHTTPError(
            status_code=response.status_code,
            category="memory_extraction_http_error",
        )

    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ProviderResponseError(
            "invalid memory extraction response"
        ) from error

    return parse_memory_extraction_message(message)

async def call_model_once(
    request: ChatRequest,
) -> ChatResponse:
    api_key = load_api_key()

    headers = build_headers(api_key)

    body = build_request_body(
        request,
        DEEPSEEK_MODEL,
    )

    try:
        async with httpx.AsyncClient(
            timeout = 30.0
        ) as client:
            response = await client.post(
                DEEPSEEK_URL,
                headers = headers,
                json = body,
            )

    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(
            "模型请求超时"
        ) from exc

    except httpx.RequestError as exc:
        raise ProviderNetworkError(
            "模型供应商网络请求失败"
        ) from exc

    if not response.is_success:
        raise ProviderHTTPError(
            f"模型供应商返回 HTTP {response.status_code}"
        )

    try:
        payload = response.json()

    except ValueError as exc:
        raise ProviderResponseError(
            "供应商返回的响应不是有效 JSON"
        ) from exc

    return parse_provider_payload(payload)

async def create_agent_message(
    messages: list[
        dict[str, Any]
    ],
    tools: list[
        dict[str, object]
    ],
) -> dict[str, Any]:
    api_key = load_api_key()

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": AGENT_MAX_OUTPUT_TOKENS,
    }

    headers = build_headers(api_key)

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
        ) as client:
            response = await client.post(
                DEEPSEEK_URL,
                json=payload,
                headers=headers,
            )

    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(
            "model timeout"
        ) from exc

    except httpx.RequestError as exc:
        raise ProviderNetworkError(
            "model network error"
        ) from exc

    if not response.is_success:
        raise ProviderHTTPError(
            status_code=response.status_code,
            category="provider_http_error",
        )

    try:
        payload = response.json()
        message = (
            payload["choices"][0]["message"]
        )

    except (
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        raise ProviderResponseError(
            "invalid provider response"
        ) from exc

    if not isinstance(message, dict):
        raise ProviderResponseError(
            "invalid message"
        )

    return message

SUMMARY_SYSTEM_PROMPT = """
你负责压缩对话历史。只根据输入生成中文摘要，不回答用户的新问题。
必须保留：用户目标和约束；已确认的视频、UP主和标识符；
未完成事项、工具错误及不确定信息。禁止补充输入中没有的事实。
""".strip()

async def create_conversation_summary(
    summary_input: str,
    max_tokens: int,
) -> str:
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SUMMARY_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": summary_input,
            }
        ],
        "max_tokens": max_tokens
    }

    headers = build_headers(load_api_key())
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                DEEPSEEK_URL,
                json=payload,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("model timeout") from exc
    except httpx.RequestError as exc:
        raise ProviderNetworkError("model network error") from exc

    if not response.is_success:
        raise ProviderHTTPError(
            status_code=response.status_code,
            category="provider_http_error",
        )

    try:
        response_payload = response.json()
        content = response_payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderResponseError("invalid provider response") from exc

    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseError("invalid summary content")
    return content.strip()
