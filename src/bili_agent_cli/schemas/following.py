from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FollowingFeedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: str | None = Field(default=None, min_length=1)


class FollowingAuthor(BaseModel):
    mid: str
    name: str
    avatar_url: str


class FollowingVideoStats(BaseModel):
    views: str | None = None
    danmaku: str | None = None


class FollowingDynamicStats(BaseModel):
    likes: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)


class FollowingVideo(BaseModel):
    bvid: str
    cid: str | None
    title: str
    cover_url: str
    stats: FollowingVideoStats = Field(default_factory=FollowingVideoStats)


class FollowingVideoItem(BaseModel):
    dynamic_id: str
    published_at: datetime
    author: FollowingAuthor
    video: FollowingVideo
    dynamic_stats: FollowingDynamicStats = Field(
        default_factory=FollowingDynamicStats
    )


class FollowingFeedResponse(BaseModel):
    items: list[FollowingVideoItem]
    has_more: bool
    next_offset: str | None


class FollowingNotFoundError(Exception):
    pass
