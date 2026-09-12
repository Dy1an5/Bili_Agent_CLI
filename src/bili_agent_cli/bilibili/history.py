from __future__ import annotations

from typing import Any

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    DEFAULT_AVATAR_URL,
    as_record,
    normalize_image_url,
    read_int,
    read_non_empty_string,
    read_non_negative_int,
    read_optional_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.schemas.history import (
    HistoryQuery,
    HistoryResponse,
    HistoryVideo,
    HistoryVideoAuthor,
)


HISTORY_URL = "https://api.bilibili.com/x/web-interface/history/cursor"


class HistoryError(Exception):
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


def _request_headers(sessdata_cookie: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Cookie": sessdata_cookie,
        "Referer": BILIBILI_REFERER,
        "User-Agent": BILIBILI_USER_AGENT,
    }


async def _request_history(
    client: httpx.AsyncClient,
    query: HistoryQuery,
    sessdata_cookie: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            HISTORY_URL,
            params={
                "type": "archive",
                "ps": query.page_size,
                "max": query.max,
                "view_at": query.view_at,
            },
            headers=_request_headers(sessdata_cookie),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise HistoryError(
            f"观看历史接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise HistoryError("观看历史接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise HistoryError("观看历史接口返回了无法解析的 JSON") from error

    if payload is None:
        raise HistoryError("观看历史接口返回格式不正确")

    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise HistoryError("观看历史接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise HistoryError(f"观看历史接口错误 {code}: {message}", code=code)

    return payload


def _parse_video(value: Any) -> HistoryVideo | None:
    item = as_record(value)
    if item is None:
        return None

    history = as_record(item.get("history"))
    if history is None or history.get("business") != "archive":
        return None

    bvid = read_non_empty_string(history.get("bvid"))
    title = read_non_empty_string(item.get("title"))
    viewed_at = read_positive_timestamp(item.get("view_at"))
    if bvid is None or title is None or viewed_at is None:
        return None

    author_name = read_non_empty_string(item.get("author_name")) or "未知UP主"
    raw_is_favorite = read_int(item.get("is_fav"))

    return HistoryVideo(
        bvid=bvid,
        cid=read_positive_id(history.get("cid")),
        title=title,
        cover_url=normalize_image_url(item.get("cover")),
        viewed_at=viewed_at,
        progress_seconds=read_int(item.get("progress")),
        duration_seconds=read_non_negative_int(item.get("duration")),
        is_favorite=raw_is_favorite == 1,
        author=HistoryVideoAuthor(
            mid=read_positive_id(item.get("author_mid")),
            name=author_name,
            avatar_url=(
                normalize_image_url(item.get("author_face"))
                or DEFAULT_AVATAR_URL
            ),
        ),
    )


def _extract_next_cursor(values: list[Any]) -> tuple[int, int] | None:
    for value in reversed(values):
        item = as_record(value)
        if item is None:
            continue
        history = as_record(item.get("history"))
        if history is None:
            continue
        oid = read_optional_non_negative_int(history.get("oid"))
        view_at = read_positive_timestamp(item.get("view_at"))
        if oid is not None and oid > 0 and view_at is not None:
            return oid, view_at
    return None


async def fetch_watch_history(
    query: HistoryQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> HistoryResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        payload = await _request_history(
            request_client,
            query,
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))
    raw_items = data.get("list") if data is not None else None
    if not isinstance(raw_items, list):
        raise HistoryError("观看历史接口返回格式不正确")

    videos = [
        video
        for value in raw_items
        if (video := _parse_video(value)) is not None
    ]
    cursor = _extract_next_cursor(raw_items)
    cursor_advanced = (
        cursor is not None
        and cursor != (query.max, query.view_at)
    )
    has_more = bool(raw_items) and cursor_advanced

    return HistoryResponse(
        videos=videos,
        page_size=query.page_size,
        has_more=has_more,
        next_max=cursor[0] if has_more and cursor is not None else None,
        next_view_at=cursor[1] if has_more and cursor is not None else None,
    )
