from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

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


class FavoriteFolderPrivacy(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


def _normalize_unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized not in result:
            result.append(normalized)
    return result


class SaveVideosToFavoriteFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_title: str = Field(
        min_length=1,
        max_length=20,
        description="目标收藏夹名称；精确匹配，不存在时自动创建。",
    )
    bvids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="需要添加的 Bilibili 视频 BVID 列表，最多 100 个。",
    )
    privacy: FavoriteFolderPrivacy = Field(
        default=FavoriteFolderPrivacy.PRIVATE,
        description="仅新建收藏夹时生效；private 私密，public 公开。",
    )

    @field_validator("folder_title")
    @classmethod
    def normalize_folder_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("folder_title 不能为空")
        return title

    @field_validator("bvids")
    @classmethod
    def normalize_bvids(cls, values: list[str]) -> list[str]:
        normalized = _normalize_unique_strings(values)
        if not normalized:
            raise ValueError("bvids 不能为空")
        for bvid in normalized:
            if not (
                bvid.startswith("BV")
                and 8 <= len(bvid) <= 22
                and bvid.isalnum()
            ):
                raise ValueError("bvid 格式不正确")
        return normalized


class PrepareFavoriteSaveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_title: str = Field(
        min_length=1,
        max_length=20,
        description="目标收藏夹名称；精确匹配，不存在时将在确认后创建。",
    )
    source_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description=(
            "此前内容工具返回的可信视频 source_id 列表，最多 100 个；"
            "不能自行构造。"
        ),
    )
    privacy: FavoriteFolderPrivacy = Field(
        default=FavoriteFolderPrivacy.PRIVATE,
        description="仅新建收藏夹时生效；默认 private。",
    )

    @field_validator("folder_title")
    @classmethod
    def normalize_folder_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("folder_title 不能为空")
        return title

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        normalized = _normalize_unique_strings(values)
        if not normalized:
            raise ValueError("source_ids 不能为空")
        for source_id in normalized:
            prefix = "bilibili:video:"
            bvid = source_id.removeprefix(prefix)
            if (
                not source_id.startswith(prefix)
                or not bvid.startswith("BV")
                or not bvid.isalnum()
                or not 8 <= len(bvid) <= 22
            ):
                raise ValueError("source_id 格式不正确")
        return normalized


class CommitFavoriteSaveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: UUID = Field(
        description="预检工具返回的一次性确认 ID。",
    )


class FavoriteSaveVideo(BaseModel):
    bvid: str
    aid: str
    title: str | None = None


class FavoriteSavePlan(BaseModel):
    folder_title: str
    existing_folder_id: str | None = None
    will_create_folder: bool
    privacy: FavoriteFolderPrivacy
    videos: list[FavoriteSaveVideo]


class FavoriteSavePreviewResponse(FavoriteSavePlan):
    confirmation_id: UUID
    expires_at: datetime


class FavoriteSaveStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FavoriteSaveTargetFolder(BaseModel):
    id: str
    title: str
    created: bool


class FavoriteSaveResponse(BaseModel):
    status: FavoriteSaveStatus
    folder: FavoriteSaveTargetFolder
    videos: list[FavoriteSaveVideo]
    retry_videos: list[FavoriteSaveVideo] = Field(default_factory=list)
    requested_count: int = Field(ge=1)
    added_count: int | None = Field(default=None, ge=0)
    upstream_code: int | None = None
    upstream_message: str | None = None
    retryable: bool
