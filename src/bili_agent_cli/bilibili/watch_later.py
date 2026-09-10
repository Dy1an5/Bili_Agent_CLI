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
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.bilibili.wbi import (
    WbiSigningError,
    fetch_wbi_mixin_key,
    sign_wbi_parameters,
)
from bili_agent_cli.schemas.watch_later import (
    WatchLaterQuery,
    WatchLaterResponse,
    WatchLaterVideo,
    WatchLaterVideoAuthor,
)


WATCH_LATER_URL = "https://api.bilibili.com/x/v2/history/toview/web"


class WatchLaterError(Exception):
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


def _parse_video(value: Any) -> WatchLaterVideo | None:
    video = as_record(value)

    if video is None:
        return None

    owner = as_record(video.get("owner"))
    bvid = read_non_empty_string(video.get("bvid"))
    cid = read_positive_id(video.get("cid"))
    title = read_non_empty_string(video.get("title"))
    cover_url = normalize_image_url(video.get("pic"))
    published_at = read_positive_timestamp(video.get("pubdate"))

    if (
        bvid is None
        or title is None
        or cover_url is None
        or published_at is None
    ):
        return None

    author_mid = read_positive_id(owner.get("mid")) if owner else None
    author_name = (
        read_non_empty_string(owner.get("name")) if owner else None
    ) or "未知UP主"
    avatar_url = (
        normalize_image_url(owner.get("face")) if owner else None
    ) or DEFAULT_AVATAR_URL

    return WatchLaterVideo(
        bvid=bvid,
        cid=cid,
        title=title,
        cover_url=cover_url,
        duration_seconds=read_non_negative_int(video.get("duration")),
        progress_seconds=read_int(video.get("progress")),
        published_at=published_at,
        author=WatchLaterVideoAuthor(
            mid=author_mid,
            name=author_name,
            avatar_url=avatar_url,
        ),
    )


async def _request_watch_later(
    client: httpx.AsyncClient,
    parameters: dict[str, str],
    sessdata_cookie: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            WATCH_LATER_URL,
            params=parameters,
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise WatchLaterError(
            f"稍后再看接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise WatchLaterError("稍后再看接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise WatchLaterError("稍后再看接口返回了无法解析的 JSON") from error

    if payload is None:
        raise WatchLaterError("稍后再看接口返回格式不正确")

    code = payload.get("code")

    if not isinstance(code, int) or isinstance(code, bool):
        raise WatchLaterError("稍后再看接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise WatchLaterError(
            f"稍后再看接口错误 {code}: {message}",
            code=code,
        )

    return payload


async def fetch_watch_later(
    query: WatchLaterQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> WatchLaterResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        try:
            mixin_key = await fetch_wbi_mixin_key(
                request_client,
                sessdata_cookie,
            )
        except WbiSigningError as error:
            raise WatchLaterError(str(error), code=error.code) from error

        parameters = sign_wbi_parameters(
            {
                "pn": query.page,
                "ps": query.page_size,
                "viewed": 0,
                "key": "",
                "asc": query.ascending,
                "need_split": True,
                "web_location": 333.881,
            },
            mixin_key,
        )
        payload = await _request_watch_later(
            request_client,
            parameters,
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))
    raw_videos_value = data.get("list") if data is not None else None
    raw_videos = [] if raw_videos_value is None else raw_videos_value

    if data is None or not isinstance(raw_videos, list):
        raise WatchLaterError("稍后再看列表返回格式不正确")

    videos = [
        video
        for value in raw_videos
        if (video := _parse_video(value)) is not None
    ]
    total_count = read_non_negative_int(data.get("count"), len(videos))
    return WatchLaterResponse(
        videos=videos,
        total_count=total_count,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total_count,
    )
