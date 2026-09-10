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


class FollowingVideo(BaseModel):
    bvid: str
    cid: str | None
    title: str
    cover_url: str


class FollowingVideoItem(BaseModel):
    dynamic_id: str
    published_at: datetime
    author: FollowingAuthor
    video: FollowingVideo


class FollowingFeedResponse(BaseModel):
    items: list[FollowingVideoItem]
    has_more: bool
    next_offset: str | None

class FollowingNotFoundError(Exception):
    pass
