import httpx

from typing import Any
from pydantic import ValidationError

from .config import (
    AGENT_MAX_OUTPUT_TOKENS,
    DEEPSEEK_URL,
    DEEPSEEK_MODEL,
    load_api_key
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
