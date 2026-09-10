from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlencode

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    DEFAULT_AVATAR_URL,
    as_record,
    normalize_image_url,
    read_non_empty_string,
    read_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.bilibili.wbi import (
    WbiSigningError,
    fetch_wbi_mixin_key,
    sign_wbi_parameters,
)
from bili_agent_cli.schemas.search import (
    SearchVideoAuthor,
    SearchVideoItem,
    SearchVideoQuery,
    SearchVideoResponse,
    SearchVideoStats,
)


SEARCH_VIDEO_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
SEARCH_REFERER_URL = "https://search.bilibili.com/video"
SEARCH_ORIGIN = "https://search.bilibili.com"
SEARCH_HIGHLIGHT_PATTERN = re.compile(r"<[^>]*>")


class SearchVideoError(Exception):
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


def _clean_search_text(value: Any) -> str | None:
    text = read_non_empty_string(value)

    if text is None:
        return None

    without_tags = SEARCH_HIGHLIGHT_PATTERN.sub("", text)
    cleaned = html.unescape(without_tags).strip()
    return cleaned or None


def _parse_duration(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)

    text = read_non_empty_string(value)

    if text is None:
        return 0

    parts = re.split(r"[:：]", text)

    if not parts or len(parts) > 3 or any(not part.isdigit() for part in parts):
        return 0

    seconds = 0

    for part in parts:
        seconds = seconds * 60 + int(part)

    return seconds


def _parse_video(value: Any) -> SearchVideoItem | None:
    video = as_record(value)

    if video is None:
        return None

    aid = read_positive_id(video.get("aid"))
    bvid = read_non_empty_string(video.get("bvid"))
    title = _clean_search_text(video.get("title"))
    cover_url = normalize_image_url(video.get("pic"))
    published_at = read_positive_timestamp(video.get("pubdate"))
    author_mid = read_positive_id(video.get("mid"))
    author_name = read_non_empty_string(video.get("author"))

    if not all(
        (
            aid,
            bvid,
            title,
            cover_url,
            published_at,
            author_mid,
            author_name,
        )
    ):
        return None

    return SearchVideoItem(
        aid=aid,
        bvid=bvid,
        title=title,
        description=_clean_search_text(video.get("description")) or "",
        cover_url=cover_url,
        duration_seconds=_parse_duration(video.get("duration")),
        published_at=published_at,
        author=SearchVideoAuthor(
            mid=author_mid,
            name=author_name,
            avatar_url=(
                normalize_image_url(video.get("upic")) or DEFAULT_AVATAR_URL
            ),
        ),
        stats=SearchVideoStats(
            views=read_non_negative_int(video.get("play")),
            danmaku=read_non_negative_int(video.get("danmaku")),
            favorites=read_non_negative_int(video.get("favorite")),
            replies=read_non_negative_int(video.get("review")),
            likes=read_non_negative_int(video.get("like")),
        ),
    )


def _build_parameters(query: SearchVideoQuery) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "search_type": "video",
        "keyword": query.keyword,
        "page": query.page,
        "page_size": query.page_size,
        "order": query.order.value,
        "duration": query.duration.value,
        "platform": "pc",
        "web_location": 1430654,
    }

    if query.tid is not None:
        parameters["tids"] = query.tid

    if query.published_after is not None:
        parameters["pubtime_begin_s"] = int(query.published_after.timestamp())

    if query.published_before is not None:
        parameters["pubtime_end_s"] = int(query.published_before.timestamp())

    return parameters


async def _request_search_videos(
    client: httpx.AsyncClient,
    parameters: dict[str, str],
    keyword: str,
    sessdata_cookie: str,
) -> dict[str, Any]:
    referer = f"{SEARCH_REFERER_URL}?{urlencode({'keyword': keyword})}"

    try:
        response = await client.get(
            SEARCH_VIDEO_URL,
            params=parameters,
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Origin": SEARCH_ORIGIN,
                "Referer": referer,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise SearchVideoError(
            f"视频搜索接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise SearchVideoError("视频搜索接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise SearchVideoError("视频搜索接口返回了无法解析的 JSON") from error

    if payload is None:
        raise SearchVideoError("视频搜索接口返回格式不正确")

    code = payload.get("code")

    if not isinstance(code, int) or isinstance(code, bool):
        raise SearchVideoError("视频搜索接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise SearchVideoError(f"视频搜索接口错误 {code}: {message}", code=code)

    return payload


async def search_videos(
    query: SearchVideoQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> SearchVideoResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        try:
            mixin_key = await fetch_wbi_mixin_key(
                request_client,
                sessdata_cookie,
            )
        except WbiSigningError as error:
            raise SearchVideoError(str(error), code=error.code) from error

        parameters = sign_wbi_parameters(_build_parameters(query), mixin_key)
        payload = await _request_search_videos(
            request_client,
            parameters,
            query.keyword,
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))

    if data is None:
        raise SearchVideoError("视频搜索结果返回格式不正确")

    if read_non_empty_string(data.get("v_voucher")) is not None:
        raise SearchVideoError("视频搜索触发 B站风控，需要完成人机验证")

    raw_videos_value = data.get("result")
    raw_videos = [] if raw_videos_value is None else raw_videos_value

    if not isinstance(raw_videos, list):
        raise SearchVideoError("视频搜索结果列表格式不正确")

    videos = [
        video
        for value in raw_videos
        if (video := _parse_video(value)) is not None
    ]
    total_count = read_non_negative_int(data.get("numResults"), len(videos))
    return SearchVideoResponse(
        videos=videos,
        total_count=total_count,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total_count,
    )
