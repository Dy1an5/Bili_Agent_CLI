from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_size: int = Field(default=20, ge=1, le=30)
    max: int = Field(default=0, ge=0)
    view_at: int = Field(default=0, ge=0)


class HistoryVideoAuthor(BaseModel):
    mid: str | None
    name: str
    avatar_url: str | None = None


class HistoryVideo(BaseModel):
    bvid: str
    cid: str | None
    title: str
    cover_url: str | None = None
    viewed_at: datetime
    progress_seconds: int
    duration_seconds: int
    is_favorite: bool
    author: HistoryVideoAuthor


class HistoryResponse(BaseModel):
    videos: list[HistoryVideo]
    page_size: int
    has_more: bool
    next_max: int | None = None
    next_view_at: int | None = None
