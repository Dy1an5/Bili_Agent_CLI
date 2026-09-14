from __future__ import annotations

import hashlib
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import as_record, read_non_empty_string


WBI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
MIXIN_KEY_ENCODE_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
)
FORBIDDEN_VALUE_CHARACTERS = str.maketrans("", "", "!'()*")


class WbiSigningError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _file_stem(url: str) -> str | None:
    name = PurePosixPath(urlsplit(url).path).name

    if "." not in name:
        return None

    stem = name.rsplit(".", 1)[0]
    return stem or None


def create_mixin_key(img_key: str, sub_key: str) -> str:
    source = img_key + sub_key

    if any(index >= len(source) for index in MIXIN_KEY_ENCODE_TABLE):
        raise WbiSigningError("WBI 密钥长度不正确")

    return "".join(source[index] for index in MIXIN_KEY_ENCODE_TABLE)


def _stringify_parameter(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()

    return str(value)


def sign_wbi_parameters(
    parameters: dict[str, Any],
    mixin_key: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    signed_parameters = {
        key: _stringify_parameter(value).translate(FORBIDDEN_VALUE_CHARACTERS)
        for key, value in parameters.items()
        if value is not None and key not in {"w_rid", "wts"}
    }
    signed_parameters["wts"] = str(
        int(time.time()) if timestamp is None else timestamp
    )
    sorted_parameters = dict(sorted(signed_parameters.items()))
    query = urlencode(sorted_parameters, quote_via=quote)
    signature = hashlib.md5(
        f"{query}{mixin_key}".encode("utf-8")
    ).hexdigest()
    sorted_parameters["w_rid"] = signature
    return sorted_parameters


async def fetch_wbi_mixin_key(
    client: httpx.AsyncClient,
    sessdata_cookie: str,
) -> str:
    try:
        response = await client.get(
            WBI_NAV_URL,
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise WbiSigningError(
            f"WBI 密钥请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise WbiSigningError("WBI 密钥网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise WbiSigningError("WBI 密钥接口返回了无法解析的 JSON") from error

    if (
        payload is None
        or not isinstance(payload.get("code"), int)
        or isinstance(payload.get("code"), bool)
    ):
        raise WbiSigningError("WBI 密钥接口返回格式不正确")

    code = payload["code"]

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise WbiSigningError(
            f"WBI 密钥接口错误 {code}: {message}",
            code=code,
        )

    data = as_record(payload.get("data"))
    wbi_image = as_record(data.get("wbi_img")) if data else None
    img_url = read_non_empty_string(wbi_image.get("img_url")) if wbi_image else None
    sub_url = read_non_empty_string(wbi_image.get("sub_url")) if wbi_image else None

    if img_url is None or sub_url is None:
        raise WbiSigningError("WBI 密钥接口缺少 wbi_img")

    img_key = _file_stem(img_url)
    sub_key = _file_stem(sub_url)

    if img_key is None or sub_key is None:
        raise WbiSigningError("WBI 密钥地址格式不正确")

    return create_mixin_key(img_key, sub_key)
