from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from .time import BeijingTime


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoIdentity(ContentModel):
    bvid: str = Field(min_length=1)
    cid: str = Field(pattern=r"^[1-9]\d*$")

    @field_validator("bvid")
    @classmethod
    def normalize_bvid(cls, value: str) -> str:
        bvid = value.strip()
        if not bvid:
            raise ValueError("bvid 不能为空")
        return bvid

    @computed_field
    @property
    def source_id(self) -> str:
        return f"bilibili:video:{self.bvid}:part:{self.cid}"


class VideoAuthor(ContentModel):
    mid: str | None = None
    name: str | None = None
    avatar_url: str | None = None


class VideoDetail(ContentModel):
    aid: str | None = None
    title: str | None = None
    description: str | None = None
    cover_url: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    published_at: BeijingTime | None = None
    is_default_part: bool | None = None


class VideoFeedback(ContentModel):
    views: int | None = Field(default=None, ge=0)
    danmaku: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)


class DynamicFeedback(ContentModel):
    likes: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)


class FavoriteVideoContext(ContentModel):
    folder_id: str
    folder_title: str | None = None
    favorited_at: BeijingTime | None = None


class WatchLaterVideoContext(ContentModel):
    progress_seconds: int | None = None


class DynamicVideoContext(ContentModel):
    dynamic_id: str
    published_at: BeijingTime | None = None
    publisher: VideoAuthor | None = None
    is_pinned: bool | None = None
    feedback: DynamicFeedback = Field(default_factory=DynamicFeedback)


class HistoryVideoContext(ContentModel):
    viewed_at: BeijingTime
    progress_seconds: int
    is_favorite: bool | None = None


class SearchVideoContext(ContentModel):
    keyword: str
    page: int = Field(ge=1)
    rank: int = Field(ge=1)
    order: str
    duration: int
    tid: int | None = None
    published_after: BeijingTime | None = None
    published_before: BeijingTime | None = None


class VideoContexts(ContentModel):
    favorite: FavoriteVideoContext | None = None
    watch_later: WatchLaterVideoContext | None = None
    dynamic: DynamicVideoContext | None = None
    history: HistoryVideoContext | None = None
    search: SearchVideoContext | None = None


class VideoRecord(ContentModel):
    identity: VideoIdentity
    author: VideoAuthor = Field(default_factory=VideoAuthor)
    detail: VideoDetail = Field(default_factory=VideoDetail)
    feedback: VideoFeedback = Field(default_factory=VideoFeedback)
    contexts: VideoContexts = Field(default_factory=VideoContexts)
    observed_at: BeijingTime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @computed_field
    @property
    def source_id(self) -> str:
        return self.identity.source_id
