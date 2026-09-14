from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.content.models import VideoRecord


SearchKeyword = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class VideoSearchOrder(StrEnum):
    RELEVANCE = "totalrank"
    MOST_VIEWED = "click"
    NEWEST = "pubdate"
    MOST_DANMAKU = "dm"
    MOST_FAVORITED = "stow"
    MOST_REPLIED = "scores"


class VideoSearchDuration(IntEnum):
    ANY = 0
    UNDER_TEN_MINUTES = 1
    TEN_TO_THIRTY_MINUTES = 2
    THIRTY_TO_SIXTY_MINUTES = 3
    OVER_SIXTY_MINUTES = 4


class SearchVideoQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: SearchKeyword = Field(description="视频搜索关键词")
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(
        default=20,
        ge=1,
        le=50,
        description="每页结果数量",
    )
    order: VideoSearchOrder = Field(
        default=VideoSearchOrder.RELEVANCE,
        description=(
            "排序：totalrank 相关性、click 播放量、pubdate 发布时间、"
            "dm 弹幕数、stow 收藏数、scores 评论数"
        ),
    )
    duration: VideoSearchDuration = Field(
        default=VideoSearchDuration.ANY,
        description=(
            "时长：0 不限、1 小于10分钟、2 为10至30分钟、"
            "3 为30至60分钟、4 超过60分钟"
        ),
    )
    tid: int | None = Field(
        default=None,
        gt=0,
        description="可选的 Bilibili 内容分区 ID",
    )
    published_after: AwareDatetime | None = Field(
        default=None,
        description="可选的最早发布时间，ISO 8601 且必须包含时区",
    )
    published_before: AwareDatetime | None = Field(
        default=None,
        description="可选的最晚发布时间，ISO 8601 且必须包含时区",
    )

    @model_validator(mode="after")
    def validate_published_range(self) -> SearchVideoQuery:
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after 不能晚于 published_before")

        return self


class SearchVideoAuthor(BaseModel):
    mid: str
    name: str
    avatar_url: str


class SearchVideoStats(VideoStats):
    pass


class SearchVideoItem(BaseModel):
    aid: str
    bvid: str
    title: str
    description: str
    cover_url: str
    duration_seconds: int
    published_at: AwareDatetime
    author: SearchVideoAuthor
    stats: SearchVideoStats


class SearchVideoResponse(BaseModel):
    videos: list[SearchVideoItem]
    total_count: int
    page: int
    page_size: int
    has_more: bool
    video_records: list[VideoRecord] = Field(default_factory=list, exclude=True)
    ingestion_applied: bool = Field(default=False, exclude=True)
    ingestion_status: str = Field(default="completed", exclude=True)
    ingestion_skipped_count: int = Field(default=0, ge=0, exclude=True)
