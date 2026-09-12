from __future__ import annotations

from typing import Any

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    DEFAULT_AVATAR_URL,
    as_record,
    normalize_image_url,
    read_non_empty_string,
    read_non_negative_int,
    read_optional_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
    FavoriteVideosQuery,
)


FAVORITE_FOLDERS_URL = (
    "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
)
FAVORITE_VIDEOS_URL = "https://api.bilibili.com/x/v3/fav/resource/list"


class FavoritesError(Exception):
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


async def _request_favorites(
    client: httpx.AsyncClient,
    url: str,
    parameters: dict[str, str | int],
    sessdata_cookie: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            url,
            params=parameters,
            headers=_request_headers(sessdata_cookie),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise FavoritesError(
            f"收藏接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise FavoritesError("收藏接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise FavoritesError("收藏接口返回了无法解析的 JSON") from error

    if payload is None:
        raise FavoritesError("收藏接口返回格式不正确")

    code = payload.get("code")

    if not isinstance(code, int) or isinstance(code, bool):
        raise FavoritesError("收藏接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise FavoritesError(f"收藏接口错误 {code}: {message}", code=code)

    return payload


def _parse_folder(value: Any) -> FavoriteFolder | None:
    folder = as_record(value)

    if folder is None:
        return None

    folder_id = read_positive_id(folder.get("id"))
    title = read_non_empty_string(folder.get("title"))

    if folder_id is None or title is None:
        return None

    return FavoriteFolder(
        id=folder_id,
        title=title,
        cover_url=normalize_image_url(folder.get("cover")),
        media_count=read_non_negative_int(folder.get("media_count")),
    )


def _parse_video(value: Any, folder_id: str) -> FavoriteVideo | None:
    video = as_record(value)

    if video is None:
        return None

    upper = as_record(video.get("upper"))
    bvid = (
        read_non_empty_string(video.get("bvid"))
        or read_non_empty_string(video.get("bv_id"))
    )
    title = read_non_empty_string(video.get("title"))
    cover_url = normalize_image_url(video.get("cover"))

    if bvid is None or title is None or cover_url is None:
        return None

    author_mid = read_positive_id(upper.get("mid")) if upper else None
    author_name = (
        read_non_empty_string(upper.get("name")) if upper else None
    ) or "未知UP主"
    avatar_url = (
        normalize_image_url(upper.get("face")) if upper else None
    ) or DEFAULT_AVATAR_URL
    count_info = as_record(video.get("cnt_info"))

    return FavoriteVideo(
        folder_id=folder_id,
        bvid=bvid,
        title=title,
        description=read_non_empty_string(video.get("intro")),
        cover_url=cover_url,
        duration_seconds=read_non_negative_int(video.get("duration")),
        favorited_at=read_positive_timestamp(video.get("fav_time")),
        author=FavoriteVideoAuthor(
            mid=author_mid,
            name=author_name,
            avatar_url=avatar_url,
        ),
        stats=VideoStats(
            views=(
                read_optional_non_negative_int(count_info.get("play"))
                if count_info
                else None
            ),
            danmaku=(
                read_optional_non_negative_int(count_info.get("danmaku"))
                if count_info
                else None
            ),
        ),
    )


async def fetch_favorite_folders(
    user_mid: str,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> FavoriteFolderListResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        payload = await _request_favorites(
            request_client,
            FAVORITE_FOLDERS_URL,
            {"up_mid": user_mid},
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))
    raw_folders = data.get("list") if data is not None else None

    if not isinstance(raw_folders, list):
        raise FavoritesError("收藏夹列表返回格式不正确")

    folders = [
        folder
        for value in raw_folders
        if (folder := _parse_folder(value)) is not None
    ]
    return FavoriteFolderListResponse(folders=folders)


async def fetch_favorite_folder_videos(
    folder_id: str,
    query: FavoriteVideosQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> FavoriteFolderVideosResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        payload = await _request_favorites(
            request_client,
            FAVORITE_VIDEOS_URL,
            {
                "media_id": folder_id,
                "pn": query.page,
                "ps": query.page_size,
                "keyword": "",
                "order": "mtime",
                "type": 0,
                "tid": 0,
                "platform": "web",
            },
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))
    folder = _parse_folder(data.get("info")) if data is not None else None
    raw_videos_value = data.get("medias") if data is not None else None
    raw_videos = [] if raw_videos_value is None else raw_videos_value

    if folder is None or not isinstance(raw_videos, list):
        raise FavoritesError("收藏夹视频列表返回格式不正确")

    videos = [
        video
        for value in raw_videos
        if (video := _parse_video(value, folder.id)) is not None
    ]
    raw_has_more = data.get("has_more")
    has_more = (
        raw_has_more
        if isinstance(raw_has_more, bool)
        else len(raw_videos) >= query.page_size
    )
    return FavoriteFolderVideosResponse(
        folder=folder,
        videos=videos,
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
    )
