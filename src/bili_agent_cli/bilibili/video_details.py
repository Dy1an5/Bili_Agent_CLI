from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    as_record,
    normalize_image_url,
    read_non_empty_string,
    read_optional_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.content.models import (
    VideoAuthor,
    VideoContexts,
    VideoDetail,
    VideoFeedback,
    VideoIdentity,
    VideoRecord,
)


VIDEO_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"


class VideoDetailError(Exception):
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


async def fetch_default_video_record(
    bvid: str,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> VideoRecord:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)
    try:
        try:
            response = await request_client.get(
                VIDEO_VIEW_URL,
                params={"bvid": bvid},
                headers={
                    "Accept": "application/json",
                    "Cookie": sessdata_cookie,
                    "Referer": BILIBILI_REFERER,
                    "User-Agent": BILIBILI_USER_AGENT,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise VideoDetailError(
                f"视频详情请求失败，HTTP {error.response.status_code}",
                status=error.response.status_code,
            ) from error
        except httpx.RequestError as error:
            raise VideoDetailError("视频详情网络请求失败") from error

        try:
            payload = as_record(response.json())
        except ValueError as error:
            raise VideoDetailError("视频详情返回了无法解析的 JSON") from error
        if payload is None:
            raise VideoDetailError("视频详情返回格式不正确")

        code = payload.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise VideoDetailError("视频详情缺少业务状态码")
        if code != 0:
            message = read_non_empty_string(payload.get("message")) or "未知错误"
            raise VideoDetailError(
                f"视频详情接口错误 {code}: {message}", code=code
            )

        data = as_record(payload.get("data"))
        if data is None:
            raise VideoDetailError("视频详情缺少 data")
        resolved_bvid = read_non_empty_string(data.get("bvid")) or bvid
        cid = read_positive_id(data.get("cid"))
        if cid is None:
            raise VideoDetailError(f"视频 {bvid} 的详情中缺少默认 cid")

        owner = as_record(data.get("owner"))
        stats = as_record(data.get("stat"))
        return VideoRecord(
            identity=VideoIdentity(bvid=resolved_bvid, cid=cid),
            author=VideoAuthor(
                mid=read_positive_id(owner.get("mid")) if owner else None,
                name=read_non_empty_string(owner.get("name")) if owner else None,
                avatar_url=(
                    normalize_image_url(owner.get("face")) if owner else None
                ),
            ),
            detail=VideoDetail(
                aid=read_positive_id(data.get("aid")),
                title=read_non_empty_string(data.get("title")),
                description=read_non_empty_string(data.get("desc")),
                cover_url=normalize_image_url(data.get("pic")),
                duration_seconds=read_optional_non_negative_int(
                    data.get("duration")
                ),
                published_at=(
                    datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    if (timestamp := read_positive_timestamp(data.get("pubdate")))
                    else None
                ),
                is_default_part=True,
            ),
            feedback=VideoFeedback(
                views=(
                    read_optional_non_negative_int(stats.get("view"))
                    if stats
                    else None
                ),
                danmaku=(
                    read_optional_non_negative_int(stats.get("danmaku"))
                    if stats
                    else None
                ),
                favorites=(
                    read_optional_non_negative_int(stats.get("favorite"))
                    if stats
                    else None
                ),
                replies=(
                    read_optional_non_negative_int(stats.get("reply"))
                    if stats
                    else None
                ),
                likes=(
                    read_optional_non_negative_int(stats.get("like"))
                    if stats
                    else None
                ),
            ),
            contexts=VideoContexts(),
        )
    finally:
        if owns_client:
            await request_client.aclose()
