from __future__ import annotations

import asyncio
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
    FavoriteFolderPrivacy,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteSavePlan,
    FavoriteSaveResponse,
    FavoriteSaveStatus,
    FavoriteSaveTargetFolder,
    FavoriteSaveVideo,
    FavoriteVideo,
    FavoriteVideoAuthor,
    FavoriteVideosQuery,
    SaveVideosToFavoriteFolderRequest,
)


FAVORITE_FOLDERS_URL = (
    "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
)
FAVORITE_VIDEOS_URL = "https://api.bilibili.com/x/v3/fav/resource/list"
FAVORITE_FOLDER_CREATE_URL = (
    "https://api.bilibili.com/x/v3/fav/folder/add"
)
FAVORITE_RESOURCE_DEAL_URL = (
    "https://api.bilibili.com/x/v3/fav/resource/deal"
)
VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
FAVORITE_WRITE_CHUNK_SIZE = 5
FAVORITE_WRITE_CHUNK_PAUSE_SECONDS = 2.0
FAVORITE_RATE_LIMIT_CODE = -702
FAVORITE_RATE_LIMIT_BACKOFF_SECONDS = (3.0, 6.0, 12.0)
FAVORITE_STOP_WRITING_CODES = {-101, -111, -403, 11203}


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


class FavoriteWriteError(FavoritesError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message, status=status, code=code)
        self.outcome_unknown = outcome_unknown


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


async def _post_favorite_write(
    client: httpx.AsyncClient,
    url: str,
    form_data: dict[str, str | int],
    write_cookie_header: str,
    resource_name: str,
) -> dict[str, Any]:
    try:
        response = await client.post(
            url,
            data=form_data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": write_cookie_header,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise FavoriteWriteError(
            f"{resource_name}请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
            outcome_unknown=True,
        ) from error
    except httpx.RequestError as error:
        raise FavoriteWriteError(
            f"{resource_name}网络请求失败",
            outcome_unknown=True,
        ) from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise FavoriteWriteError(
            f"{resource_name}返回了无法解析的 JSON",
            outcome_unknown=True,
        ) from error

    if payload is None:
        raise FavoriteWriteError(
            f"{resource_name}返回格式不正确",
            outcome_unknown=True,
        )

    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise FavoriteWriteError(
            f"{resource_name}返回格式不正确，缺少业务状态码",
            outcome_unknown=True,
        )

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise FavoriteWriteError(
            f"{resource_name}错误 {code}: {message}",
            code=code,
        )

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


def _matching_folders(
    folders: list[FavoriteFolder],
    title: str,
) -> list[FavoriteFolder]:
    return [folder for folder in folders if folder.title == title]


def _select_matching_folder(
    folders: list[FavoriteFolder],
    title: str,
) -> FavoriteFolder | None:
    matches = _matching_folders(folders, title)
    if len(matches) > 1:
        raise FavoriteWriteError(
            f"存在多个同名收藏夹“{title}”，无法安全选择目标"
        )
    return matches[0] if matches else None


async def _resolve_favorite_video(
    client: httpx.AsyncClient,
    bvid: str,
    sessdata_cookie: str,
) -> FavoriteSaveVideo:
    try:
        payload = await _request_favorites(
            client,
            VIDEO_VIEW_URL,
            {"bvid": bvid},
            sessdata_cookie,
        )
    except FavoritesError as error:
        raise FavoriteWriteError(
            str(error),
            status=error.status,
            code=error.code,
        ) from error

    data = as_record(payload.get("data"))
    aid = read_positive_id(data.get("aid")) if data is not None else None
    if aid is None:
        raise FavoriteWriteError(f"视频 {bvid} 的详情中缺少 aid")

    return FavoriteSaveVideo(
        bvid=bvid,
        aid=aid,
        title=(
            read_non_empty_string(data.get("title"))
            if data is not None
            else None
        ),
    )


async def prepare_favorite_save(
    request: SaveVideosToFavoriteFolderRequest,
    user_mid: str,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> FavoriteSavePlan:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        videos = [
            await _resolve_favorite_video(
                request_client,
                bvid,
                sessdata_cookie,
            )
            for bvid in request.bvids
        ]
        try:
            folder_response = await fetch_favorite_folders(
                user_mid,
                sessdata_cookie,
                request_client,
            )
        except FavoritesError as error:
            raise FavoriteWriteError(
                str(error),
                status=error.status,
                code=error.code,
            ) from error
        existing_folder = _select_matching_folder(
            folder_response.folders,
            request.folder_title,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    return FavoriteSavePlan(
        folder_title=request.folder_title,
        existing_folder_id=(
            existing_folder.id if existing_folder is not None else None
        ),
        will_create_folder=existing_folder is None,
        privacy=request.privacy,
        videos=videos,
    )


async def _create_favorite_folder(
    client: httpx.AsyncClient,
    title: str,
    privacy: FavoriteFolderPrivacy,
    write_cookie_header: str,
    csrf_token: str,
) -> FavoriteFolder:
    payload = await _post_favorite_write(
        client,
        FAVORITE_FOLDER_CREATE_URL,
        {
            "title": title,
            "intro": "",
            "privacy": 1 if privacy is FavoriteFolderPrivacy.PRIVATE else 0,
            "cover": "",
            "csrf": csrf_token,
        },
        write_cookie_header,
        "新建收藏夹接口",
    )
    folder = _parse_folder(payload.get("data"))
    if folder is None:
        raise FavoriteWriteError(
            "新建收藏夹接口成功响应格式不正确",
            outcome_unknown=True,
        )
    return folder


async def _load_target_folder(
    client: httpx.AsyncClient,
    plan: FavoriteSavePlan,
    user_mid: str,
    sessdata_cookie: str,
    write_cookie_header: str,
    csrf_token: str,
) -> tuple[FavoriteFolder, bool]:
    try:
        folder_response = await fetch_favorite_folders(
            user_mid,
            sessdata_cookie,
            client,
        )
    except FavoritesError as error:
        raise FavoriteWriteError(
            str(error),
            status=error.status,
            code=error.code,
        ) from error

    existing_folder = _select_matching_folder(
        folder_response.folders,
        plan.folder_title,
    )
    if existing_folder is not None:
        return existing_folder, False

    try:
        return (
            await _create_favorite_folder(
                client,
                plan.folder_title,
                plan.privacy,
                write_cookie_header,
                csrf_token,
            ),
            True,
        )
    except FavoriteWriteError as error:
        if not error.outcome_unknown:
            raise

        try:
            recovery_response = await fetch_favorite_folders(
                user_mid,
                sessdata_cookie,
                client,
            )
        except FavoritesError:
            raise error
        recovered_folder = _select_matching_folder(
            recovery_response.folders,
            plan.folder_title,
        )
        if recovered_folder is None:
            raise error
        return recovered_folder, True


async def _add_video_to_favorite_folder(
    client: httpx.AsyncClient,
    folder_id: str,
    video: FavoriteSaveVideo,
    write_cookie_header: str,
    csrf_token: str,
) -> None:
    await _post_favorite_write(
        client,
        FAVORITE_RESOURCE_DEAL_URL,
        {
            "rid": video.aid,
            "type": 2,
            "add_media_ids": folder_id,
            "del_media_ids": "",
            "csrf": csrf_token,
        },
        write_cookie_header,
        f"收藏视频 {video.bvid} 接口",
    )


async def _add_video_with_rate_limit_retry(
    client: httpx.AsyncClient,
    folder_id: str,
    video: FavoriteSaveVideo,
    write_cookie_header: str,
    csrf_token: str,
) -> None:
    for delay_seconds in (None, *FAVORITE_RATE_LIMIT_BACKOFF_SECONDS):
        if delay_seconds is not None:
            await asyncio.sleep(delay_seconds)
        try:
            await _add_video_to_favorite_folder(
                client,
                folder_id,
                video,
                write_cookie_header,
                csrf_token,
            )
            return
        except FavoriteWriteError as error:
            if (
                error.code != FAVORITE_RATE_LIMIT_CODE
                or delay_seconds == FAVORITE_RATE_LIMIT_BACKOFF_SECONDS[-1]
            ):
                raise


def _favorite_failure_fields(
    error: FavoriteWriteError,
) -> dict[str, int | str | None]:
    return {
        "upstream_code": error.code,
        "upstream_message": str(error),
    }


async def execute_favorite_save(
    plan: FavoriteSavePlan,
    user_mid: str,
    sessdata_cookie: str,
    write_cookie_header: str,
    csrf_token: str,
    client: httpx.AsyncClient | None = None,
) -> FavoriteSaveResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        folder, folder_created = await _load_target_folder(
            request_client,
            plan,
            user_mid,
            sessdata_cookie,
            write_cookie_header,
            csrf_token,
        )
        retry_videos: list[FavoriteSaveVideo] = []
        confirmed_added_count = 0
        last_error: FavoriteWriteError | None = None

        for index, video in enumerate(plan.videos):
            try:
                await _add_video_with_rate_limit_retry(
                    request_client,
                    folder.id,
                    video,
                    write_cookie_header,
                    csrf_token,
                )
            except FavoriteWriteError as error:
                last_error = error
                retry_videos.append(video)
                if error.outcome_unknown:
                    retry_videos.extend(plan.videos[index + 1:])
                    return FavoriteSaveResponse(
                        status=FavoriteSaveStatus.OUTCOME_UNKNOWN,
                        folder=FavoriteSaveTargetFolder(
                            id=folder.id,
                            title=folder.title,
                            created=folder_created,
                        ),
                        videos=plan.videos,
                        retry_videos=retry_videos,
                        requested_count=len(plan.videos),
                        added_count=None,
                        **_favorite_failure_fields(error),
                        retryable=True,
                    )
                if error.code in (
                    FAVORITE_STOP_WRITING_CODES | {FAVORITE_RATE_LIMIT_CODE}
                ):
                    retry_videos.extend(plan.videos[index + 1:])
                    break
            else:
                confirmed_added_count += 1

            attempted_count = index + 1
            if (
                attempted_count % FAVORITE_WRITE_CHUNK_SIZE == 0
                and attempted_count < len(plan.videos)
            ):
                await asyncio.sleep(FAVORITE_WRITE_CHUNK_PAUSE_SECONDS)

        if retry_videos:
            if last_error is None:
                raise RuntimeError("收藏写入失败但缺少错误信息")
            return FavoriteSaveResponse(
                status=FavoriteSaveStatus.PARTIAL,
                folder=FavoriteSaveTargetFolder(
                    id=folder.id,
                    title=folder.title,
                    created=folder_created,
                ),
                videos=plan.videos,
                retry_videos=retry_videos,
                requested_count=len(plan.videos),
                added_count=confirmed_added_count,
                **_favorite_failure_fields(last_error),
                retryable=True,
            )
    finally:
        if owns_client:
            await request_client.aclose()

    return FavoriteSaveResponse(
        status=FavoriteSaveStatus.COMPLETED,
        folder=FavoriteSaveTargetFolder(
            id=folder.id,
            title=folder.title,
            created=folder_created,
        ),
        videos=plan.videos,
        requested_count=len(plan.videos),
        added_count=len(plan.videos),
        retryable=False,
    )


async def save_videos_to_favorite_folder(
    request: SaveVideosToFavoriteFolderRequest,
    user_mid: str,
    sessdata_cookie: str,
    write_cookie_header: str,
    csrf_token: str,
    client: httpx.AsyncClient | None = None,
) -> FavoriteSaveResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)
    try:
        plan = await prepare_favorite_save(
            request,
            user_mid,
            sessdata_cookie,
            request_client,
        )
        return await execute_favorite_save(
            plan,
            user_mid,
            sessdata_cookie,
            write_cookie_header,
            csrf_token,
            request_client,
        )
    finally:
        if owns_client:
            await request_client.aclose()
