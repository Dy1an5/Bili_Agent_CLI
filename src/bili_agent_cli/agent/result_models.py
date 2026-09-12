from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, computed_field

from bili_agent_cli.agent.models import build_video_source_id
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.following import (
    FollowingDynamicStats,
    FollowingVideoStats,
)


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
    favorited_at: datetime | None
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
    published_at: datetime
    author: LlmVideoAuthor
    stats: VideoStats


class LlmWatchLaterResult(BaseModel):
    videos: list[LlmWatchLaterVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class LlmSearchVideo(LlmSourceVideo):
    aid: str
    title: str
    description: str
    duration_seconds: int
    published_at: datetime
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
    published_at: datetime
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


def project_search_videos(result: BaseModel) -> BaseModel:
    return _project(result, LlmSearchVideoResult)


def project_following_feed(result: BaseModel) -> BaseModel:
    return _project(result, LlmFollowingFeedResult)
