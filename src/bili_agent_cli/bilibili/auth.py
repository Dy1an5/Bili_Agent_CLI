from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


QR_GENERATE_URL = (
    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
)
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
BILIBILI_REFERER = "https://www.bilibili.com/"
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BilibiliLoginError(Exception):
    pass


class _QrGenerateData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    qrcode_key: str


class _QrGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int
    message: str | None = None
    data: _QrGenerateData | None = None


class _QrPollData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int
    message: str | None = None


class _QrPollResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int
    message: str | None = None
    data: _QrPollData | None = None


@dataclass(frozen=True)
class QrLoginRequest:
    url: str
    key: str


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Referer": BILIBILI_REFERER,
        "User-Agent": BILIBILI_USER_AGENT,
    }


async def _read_json(response: httpx.Response, resource_name: str) -> object:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise BilibiliLoginError(
            f"{resource_name}请求失败，HTTP {response.status_code}"
        ) from error

    try:
        return response.json()
    except ValueError as error:
        raise BilibiliLoginError(f"{resource_name}返回了无法解析的 JSON") from error


async def create_qr_login(client: httpx.AsyncClient) -> QrLoginRequest:
    response = await client.get(QR_GENERATE_URL, headers=_request_headers())
    payload = await _read_json(response, "登录二维码")

    try:
        result = _QrGenerateResponse.model_validate(payload)
    except ValidationError as error:
        raise BilibiliLoginError("登录二维码接口返回格式不正确") from error

    if result.code != 0:
        message = result.message or "未知错误"
        raise BilibiliLoginError(f"登录二维码接口错误 {result.code}: {message}")

    if result.data is None or not result.data.url or not result.data.qrcode_key:
        raise BilibiliLoginError("登录二维码接口缺少二维码数据")

    return QrLoginRequest(url=result.data.url, key=result.data.qrcode_key)


def collect_client_cookies(client: httpx.AsyncClient) -> dict[str, str]:
    cookies: dict[str, str] = {}

    for cookie in client.cookies.jar:
        if cookie.name and cookie.value:
            cookies[cookie.name] = cookie.value

    return cookies


async def wait_for_qr_login(
    client: httpx.AsyncClient,
    qr_login: QrLoginRequest,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 2.0,
    on_status_change: Callable[[int], None] | None = None,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    previous_status: int | None = None

    while time.monotonic() < deadline:
        response = await client.get(
            QR_POLL_URL,
            headers=_request_headers(),
            params={"qrcode_key": qr_login.key},
        )
        payload = await _read_json(response, "扫码登录状态")

        try:
            result = _QrPollResponse.model_validate(payload)
        except ValidationError as error:
            raise BilibiliLoginError("扫码登录状态接口返回格式不正确") from error

        if result.code != 0:
            message = result.message or "未知错误"
            raise BilibiliLoginError(f"扫码登录接口错误 {result.code}: {message}")

        if result.data is None:
            raise BilibiliLoginError("扫码登录状态接口缺少 data")

        status = result.data.code

        if status == 0:
            return collect_client_cookies(client)

        if status == 86038:
            raise BilibiliLoginError("二维码已失效，请重新运行 login 命令")

        if status not in {86090, 86101}:
            message = result.data.message or "未知错误"
            raise BilibiliLoginError(f"扫码登录失败 {status}: {message}")

        if status != previous_status:
            if on_status_change is not None:
                on_status_change(status)
            previous_status = status

        await asyncio.sleep(poll_interval_seconds)

    raise BilibiliLoginError("等待扫码登录超时，请重新运行 login 命令")
