from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.content.models import VideoRecord


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
    video_records: list[VideoRecord] = Field(default_factory=list, exclude=True)
    ingestion_applied: bool = Field(default=False, exclude=True)
    ingestion_status: str = Field(default="completed", exclude=True)
    ingestion_skipped_count: int = Field(default=0, ge=0, exclude=True)
