from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


Bvid = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=32,
        pattern=r"^BV[0-9A-Za-z]+$",
    ),
]
Cid = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[1-9]\d*$"),
]
SubtitleLanguage = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[0-9A-Za-z_-]+$",
    ),
]
SubtitleDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
SubtitleText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]


class SubtitleTrackSource(StrEnum):
    HUMAN = "bilibili-human"
    AI = "bilibili-ai"


class SubtitleStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class SubtitleUnavailableReason(StrEnum):
    NO_SUBTITLE = "no-subtitle"
    LANGUAGE_NOT_FOUND = "language-not-found"


class GetVideoSubtitleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bvid: Bvid = Field(
        description=(
            "Bilibili 视频 BV 号，应直接来自此前视频工具返回的 bvid，"
            "不要从标题猜测。"
        )
    )
    cid: Cid = Field(
        description=(
            "具体分 P 的 cid，应直接来自此前视频工具返回的 cid；"
            "单 P 视频也必须提供。"
        )
    )
    language: SubtitleLanguage | None = Field(
        default=None,
        description=(
            "可选的字幕语言代码，对应 Bilibili lan 字段，例如 zh-CN。"
            "省略时优先人工中文，其次 AI 中文、人工其他语言、AI 其他语言。"
        ),
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="字幕 cue 偏移量，从 0 开始。继续读取时使用 next_offset。",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=200,
        description="本次返回的最大字幕 cue 数，范围 1 到 200。",
    )
    refresh: bool = Field(
        default=False,
        description="是否跳过 24 小时本地缓存并从 Bilibili 重新获取字幕。",
    )


class SubtitleTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: SubtitleLanguage
    display_name: SubtitleDisplayName
    source: SubtitleTrackSource


class SubtitleCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: SubtitleText

    @model_validator(mode="after")
    def validate_time_range(self) -> SubtitleCue:
        if self.end_ms <= self.start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间")
        return self


class VideoSubtitleDocument(BaseModel):
    """完整的规范化字幕文档，用于领域层和持久化，不直接投影给模型。"""

    model_config = ConfigDict(extra="forbid")

    bvid: Bvid
    cid: Cid
    track: SubtitleTrack
    available_tracks: list[SubtitleTrack]
    cues: list[SubtitleCue]
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fetched_at: AwareDatetime


class VideoSubtitleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SubtitleStatus
    bvid: Bvid
    cid: Cid
    track: SubtitleTrack | None = None
    available_tracks: list[SubtitleTrack] = Field(default_factory=list)
    cues: list[SubtitleCue] = Field(default_factory=list)
    source_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    total_cues: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0)
    fetched_at: AwareDatetime
    cached: bool
    reason: SubtitleUnavailableReason | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> VideoSubtitleResponse:
        if self.status is SubtitleStatus.AVAILABLE:
            if self.track is None or self.source_hash is None or self.reason is not None:
                raise ValueError("可用字幕缺少轨道或内容哈希")
            if self.has_more != (self.offset + len(self.cues) < self.total_cues):
                raise ValueError("字幕分页状态不一致")
            expected_next = self.offset + len(self.cues) if self.has_more else None
            if self.next_offset != expected_next:
                raise ValueError("字幕下一页偏移量不一致")
        else:
            if (
                self.track is not None
                or self.cues
                or self.source_hash is not None
                or self.total_cues != 0
                or self.has_more
                or self.next_offset is not None
                or self.reason is None
            ):
                raise ValueError("不可用字幕的响应结构不一致")
        return self
