from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.content.models import VideoRecord


class UserDynamicsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_mid: str = Field(
        min_length=1,
        max_length=20,
        pattern=r"^[1-9]\d*$",
        description=(
            "目标 UP 主的数字 MID；可直接由用户提供，或取自 "
            "get_following_users 返回的 mid。"
        ),
    )
    offset: str = Field(
        default="",
        max_length=1000,
        description=(
            "分页游标。第一页留空；继续读取时使用上一页返回的 next_offset。"
        ),
    )


class UserDynamicAuthor(BaseModel):
    mid: str | None = None
    name: str | None = None
    avatar_url: str | None = None


class UserDynamicStats(BaseModel):
    likes: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)


class UserDynamicContent(BaseModel):
    major_type: str | None = None
    title: str | None = None
    text: str | None = None
    bvid: str | None = None
    jump_url: str | None = None
    cover_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)


class UserDynamicItem(BaseModel):
    dynamic_id: str
    type: str | None = None
    published_at: datetime | None = None
    visible: bool | None = None
    is_pinned: bool | None = None
    author: UserDynamicAuthor | None = None
    content: UserDynamicContent = Field(default_factory=UserDynamicContent)
    stats: UserDynamicStats = Field(default_factory=UserDynamicStats)
    original: UserDynamicItem | None = None


class UserDynamicsResponse(BaseModel):
    user_mid: str
    items: list[UserDynamicItem]
    total_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    pages_fetched: int = Field(ge=1)
    has_more: bool = False
    next_offset: str | None = None
    video_records: list[VideoRecord] = Field(default_factory=list, exclude=True)
    ingestion_applied: bool = Field(default=False, exclude=True)
    ingestion_status: str = Field(default="completed", exclude=True)
    ingestion_skipped_count: int = Field(default=0, ge=0, exclude=True)
