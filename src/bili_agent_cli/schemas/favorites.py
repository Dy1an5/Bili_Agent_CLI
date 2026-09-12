from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.schemas.common import VideoStats


class FavoriteFoldersQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FavoriteVideosQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=40)


class FavoriteFolderVideosQuery(FavoriteVideosQuery):
    folder_id: str = Field(pattern=r"^[1-9]\d*$")


class FavoriteFolder(BaseModel):
    id: str
    title: str
    cover_url: str | None
    media_count: int


class FavoriteFolderListResponse(BaseModel):
    folders: list[FavoriteFolder]


class FavoriteVideoAuthor(BaseModel):
    mid: str | None
    name: str
    avatar_url: str


class FavoriteVideo(BaseModel):
    folder_id: str
    bvid: str
    title: str
    description: str | None = None
    cover_url: str
    duration_seconds: int
    favorited_at: datetime | None
    author: FavoriteVideoAuthor
    stats: VideoStats = Field(default_factory=VideoStats)


class FavoriteFolderVideosResponse(BaseModel):
    folder: FavoriteFolder
    videos: list[FavoriteVideo]
    page: int
    page_size: int
    has_more: bool
