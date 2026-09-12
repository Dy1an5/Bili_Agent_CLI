from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from pydantic import BaseModel, PlainSerializer, computed_field

from bili_agent_cli.agent.models import build_video_source_id
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.following import (
    FollowingDynamicStats,
    FollowingVideoStats,
)
from bili_agent_cli.schemas.history import HistoryResponse


BEIJING_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")
BEIJING_TIME_FORMAT = "%Y-%m-%d %H:%M (UTC+8)"


def format_beijing_time(value: datetime | None) -> str | None:
    """把时间统一换算成 UTC+8 的可读文本，避免模型自行换算。"""

    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(BEIJING_TIMEZONE).strftime(BEIJING_TIME_FORMAT)


BeijingTime = Annotated[
    datetime,
    PlainSerializer(
        format_beijing_time,
        return_type=str,
        when_used="json",
    ),
]


class LlmVideoAuthor(BaseModel):
    mid: str | None
    name: str


class LlmSourceVideo(BaseModel):
    bvid: str

    @computed_field
    @property
    def source_id(self) -> str:
        return build_video_source_id(self.bvid)


class LlmFavoriteFolder(BaseModel):
    id: str
    title: str
    media_count: int


class LlmFavoriteFolderListResult(BaseModel):
    folders: list[LlmFavoriteFolder]


class LlmFavoriteVideo(LlmSourceVideo):
    folder_id: str
    title: str
    description: str | None
    duration_seconds: int
    favorited_at: BeijingTime | None
    author: LlmVideoAuthor
    stats: VideoStats


class LlmFavoriteFolderVideosResult(BaseModel):
    folder: LlmFavoriteFolder
    videos: list[LlmFavoriteVideo]
    page: int
    page_size: int
    has_more: bool


class LlmWatchLaterVideo(LlmSourceVideo):
    cid: str | None
    title: str
    duration_seconds: int
    progress_seconds: int
    published_at: BeijingTime
    author: LlmVideoAuthor
    stats: VideoStats


class LlmWatchLaterResult(BaseModel):
    videos: list[LlmWatchLaterVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class LlmHistoryVideo(LlmSourceVideo):
    cid: str | None
    title: str
    viewed_at: BeijingTime
    progress_seconds: int
    duration_seconds: int
    is_favorite: bool
    author: LlmVideoAuthor


class LlmHistoryResult(BaseModel):
    videos: list[LlmHistoryVideo]
    page_size: int
    has_more: bool
    next_max: int | None
    next_view_at: int | None


class LlmSearchVideo(LlmSourceVideo):
    aid: str
    title: str
    description: str
    duration_seconds: int
    published_at: BeijingTime
    author: LlmVideoAuthor
    stats: VideoStats


class LlmSearchVideoResult(BaseModel):
    videos: list[LlmSearchVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class LlmFollowingVideo(LlmSourceVideo):
    cid: str | None
    title: str
    stats: FollowingVideoStats


class LlmFollowingVideoItem(BaseModel):
    dynamic_id: str
    published_at: BeijingTime
    author: LlmVideoAuthor
    video: LlmFollowingVideo
    dynamic_stats: FollowingDynamicStats


class LlmFollowingFeedResult(BaseModel):
    items: list[LlmFollowingVideoItem]
    has_more: bool
    next_offset: str | None


def _project(result: BaseModel, model: type[BaseModel]) -> BaseModel:
    return model.model_validate(result.model_dump(mode="python"))


def project_favorite_folders(result: BaseModel) -> BaseModel:
    return _project(result, LlmFavoriteFolderListResult)


def project_favorite_folder_videos(result: BaseModel) -> BaseModel:
    return _project(result, LlmFavoriteFolderVideosResult)


def project_watch_later(result: BaseModel) -> BaseModel:
    return _project(result, LlmWatchLaterResult)


def project_watch_history(result: BaseModel) -> BaseModel:
    if not isinstance(result, HistoryResponse):
        raise TypeError("project_watch_history 收到了错误的结果模型")
    return _project(result, LlmHistoryResult)


def project_search_videos(result: BaseModel) -> BaseModel:
    return _project(result, LlmSearchVideoResult)


def project_following_feed(result: BaseModel) -> BaseModel:
    return _project(result, LlmFollowingFeedResult)
