from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT


BILIBILI_API_BASE_URL = "https://api.bilibili.com"


class BilibiliDebugError(Exception):
    pass


class _BilibiliResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    message: str | None = None


def validate_api_path(path: str) -> str:
    parsed = urlsplit(path)

    if (
        not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise BilibiliDebugError(
            "接口必须是 api.bilibili.com 下的绝对路径，例如 /x/web-interface/nav"
        )

    return path


async def request_bilibili_get(
    path: str,
    parameters: list[tuple[str, str]],
    sessdata_cookie: str,
) -> object:
    validated_path = validate_api_path(path)
    url = f"{BILIBILI_API_BASE_URL}{validated_path}"
    headers = {
        "Accept": "application/json",
        "Cookie": sessdata_cookie,
        "Referer": BILIBILI_REFERER,
        "User-Agent": BILIBILI_USER_AGENT,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers, params=parameters)
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise BilibiliDebugError(
            f"B站接口请求失败，HTTP {error.response.status_code}"
        ) from error
    except httpx.RequestError as error:
        raise BilibiliDebugError(f"B站接口请求失败: {error}") from error

    try:
        payload = response.json()
    except ValueError as error:
        raise BilibiliDebugError("B站接口返回了无法解析的 JSON") from error

    try:
        result = _BilibiliResponse.model_validate(payload)
    except ValidationError as error:
        raise BilibiliDebugError("B站接口返回格式不正确，缺少业务状态码") from error

    if result.code != 0:
        message = result.message or "未知错误"
        raise BilibiliDebugError(f"B站接口错误 {result.code}: {message}")

    return payload
