from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.content.models import VideoRecord


class WatchLaterQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=40)
    ascending: bool = False


class WatchLaterVideoAuthor(BaseModel):
    mid: str | None
    name: str
    avatar_url: str


class WatchLaterVideo(BaseModel):
    bvid: str
    cid: str | None
    title: str
    cover_url: str
    duration_seconds: int
    progress_seconds: int
    published_at: datetime
    author: WatchLaterVideoAuthor
    stats: VideoStats = Field(default_factory=VideoStats)


class WatchLaterResponse(BaseModel):
    videos: list[WatchLaterVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool
    video_records: list[VideoRecord] = Field(default_factory=list, exclude=True)
    ingestion_applied: bool = Field(default=False, exclude=True)
    ingestion_status: str = Field(default="completed", exclude=True)
    ingestion_skipped_count: int = Field(default=0, ge=0, exclude=True)
