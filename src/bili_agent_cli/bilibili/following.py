from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.schemas.following import (
    FollowingAuthor,
    FollowingDynamicStats,
    FollowingFeedQuery,
    FollowingFeedResponse,
    FollowingVideo,
    FollowingVideoItem,
    FollowingVideoStats,
)


FOLLOWING_FEED_URL = (
    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"
)


class FollowingFeedError(Exception):
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


class _BilibiliStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    message: str | None = None


class _BilibiliFollowingData(BaseModel):
    model_config = ConfigDict(extra="allow")

    has_more: bool
    items: list[dict[str, Any]]
    offset: str


class _BilibiliFollowingResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int
    message: str
    ttl: int
    data: _BilibiliFollowingData


def _build_parameters(query: FollowingFeedQuery) -> dict[str, str]:
    parameters = {
        "timezone_offset": "-480",
        "type": "all",
        "page": "1",
        "features": "itemOpusStyle",
    }

    if query.offset is not None:
        parameters["offset"] = query.offset

    return parameters


def _as_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    return value


def _read_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    return text or None


def _read_positive_id(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)

    if isinstance(value, str) and value.strip().isdigit():
        identifier = value.strip()
        return identifier if int(identifier) > 0 else None

    return None


def _read_positive_timestamp(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value

    if isinstance(value, str) and value.strip().isdigit():
        timestamp = int(value.strip())
        return timestamp if timestamp > 0 else None

    return None


def _read_non_negative_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value

    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())

    return None


def _read_count_text(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)

    return _read_non_empty_string(value)


def _read_dynamic_count(
    module_stat: dict[str, Any] | None,
    key: str,
) -> int | None:
    stat = _as_record(module_stat.get(key)) if module_stat else None
    return _read_non_negative_count(stat.get("count")) if stat else None


def _normalize_image_url(value: Any) -> str | None:
    url = _read_non_empty_string(value)

    if url is None:
        return None

    if url.startswith("//"):
        return f"https:{url}"

    if url.startswith("http://"):
        return f"https://{url.removeprefix('http://')}"

    return url


def _read_video_source(item: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [item, _as_record(item.get("orig"))]

    for candidate in candidates:
        if candidate is None:
            continue

        modules = _as_record(candidate.get("modules"))
        dynamic = _as_record(modules.get("module_dynamic")) if modules else None
        major = _as_record(dynamic.get("major")) if dynamic else None

        if major and major.get("type") == "MAJOR_TYPE_ARCHIVE":
            return candidate

    return None


def _parse_video_item(value: dict[str, Any]) -> FollowingVideoItem | None:
    dynamic_id = _read_positive_id(value.get("id_str"))
    source = _read_video_source(value)

    if dynamic_id is None or source is None:
        return None

    modules = _as_record(source.get("modules"))
    author = _as_record(modules.get("module_author")) if modules else None
    dynamic = _as_record(modules.get("module_dynamic")) if modules else None
    major = _as_record(dynamic.get("major")) if dynamic else None
    archive = _as_record(major.get("archive")) if major else None

    if author is None or archive is None:
        return None

    author_mid = _read_positive_id(author.get("mid"))
    author_name = _read_non_empty_string(author.get("name"))
    avatar_url = _normalize_image_url(author.get("face"))
    published_at = _read_positive_timestamp(author.get("pub_ts"))
    bvid = _read_non_empty_string(archive.get("bvid"))
    cid = _read_positive_id(archive.get("cid"))
    title = _read_non_empty_string(archive.get("title"))
    cover_url = _normalize_image_url(archive.get("cover"))
    archive_stat = _as_record(archive.get("stat"))
    module_stat = _as_record(modules.get("module_stat")) if modules else None

    if not all(
        (
            author_mid,
            author_name,
            avatar_url,
            published_at,
            bvid,
            title,
            cover_url,
        )
    ):
        return None

    return FollowingVideoItem(
        dynamic_id=dynamic_id,
        published_at=published_at,
        author=FollowingAuthor(
            mid=author_mid,
            name=author_name,
            avatar_url=avatar_url,
        ),
        video=FollowingVideo(
            bvid=bvid,
            cid=cid,
            title=title,
            cover_url=cover_url,
            stats=FollowingVideoStats(
                views=(
                    _read_count_text(archive_stat.get("play"))
                    if archive_stat
                    else None
                ),
                danmaku=(
                    _read_count_text(archive_stat.get("danmaku"))
                    if archive_stat
                    else None
                ),
            ),
        ),
        dynamic_stats=FollowingDynamicStats(
            likes=_read_dynamic_count(module_stat, "like"),
            replies=_read_dynamic_count(module_stat, "comment"),
            reposts=_read_dynamic_count(module_stat, "forward"),
            favorites=_read_dynamic_count(module_stat, "favorite"),
        ),
    )


async def _request_following_feed(
    client: httpx.AsyncClient,
    query: FollowingFeedQuery,
    sessdata_cookie: str,
) -> Any:
    try:
        response = await client.get(
            FOLLOWING_FEED_URL,
            params=_build_parameters(query),
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise FollowingFeedError(
            f"动态接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise FollowingFeedError("动态接口网络请求失败") from error

    try:
        return response.json()
    except ValueError as error:
        raise FollowingFeedError("动态接口返回了无法解析的 JSON") from error


async def fetch_following_feed(
    query: FollowingFeedQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> FollowingFeedResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        payload = await _request_following_feed(
            request_client,
            query,
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    try:
        status = _BilibiliStatus.model_validate(payload)
    except ValidationError as error:
        raise FollowingFeedError("动态接口返回格式不正确，缺少业务状态码") from error

    if status.code != 0:
        message = status.message or "未知错误"
        raise FollowingFeedError(
            f"动态接口错误 {status.code}: {message}",
            code=status.code,
        )

    try:
        response = _BilibiliFollowingResponse.model_validate(payload)
    except ValidationError as error:
        raise FollowingFeedError("动态接口成功响应格式不正确") from error

    items = [
        parsed_item
        for item in response.data.items
        if (parsed_item := _parse_video_item(item)) is not None
    ]

    return FollowingFeedResponse(
        items=items,
        has_more=response.data.has_more,
        next_offset=response.data.offset if response.data.has_more else None,
    )
