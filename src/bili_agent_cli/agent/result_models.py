from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, computed_field

from bili_agent_cli.agent.models import build_video_source_id
from bili_agent_cli.content.models import VideoRecord
from bili_agent_cli.content.time import (
    BeijingTime,
    format_beijing_time,
)
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.following_feed import (
    FollowingDynamicStats,
    FollowingVideoStats,
)
from bili_agent_cli.schemas.following_users import FollowingOfficialVerification
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderPrivacy,
    FavoriteSaveStatus,
)
from bili_agent_cli.schemas.history import HistoryResponse
from bili_agent_cli.schemas.user_dynamics import UserDynamicStats


class LlmVideoAuthor(BaseModel):
    mid: str | None
    name: str


class LlmSourceVideo(BaseModel):
    bvid: str

    @computed_field
    @property
    def source_id(self) -> str:
        return build_video_source_id(self.bvid)


class LlmFavoriteFolder(BaseModel):
    id: str
    title: str
    media_count: int


class LlmFavoriteFolderListResult(BaseModel):
    folders: list[LlmFavoriteFolder]


class LlmFavoriteVideo(LlmSourceVideo):
    folder_id: str
    title: str
    description: str | None
    duration_seconds: int
    favorited_at: BeijingTime | None
    author: LlmVideoAuthor
    stats: VideoStats


class LlmFavoriteFolderVideosResult(BaseModel):
    folder: LlmFavoriteFolder
    videos: list[LlmFavoriteVideo]
    page: int
    page_size: int
    has_more: bool


class LlmFavoriteSaveVideo(LlmSourceVideo):
    aid: str
    title: str | None


class LlmFavoriteSavePreviewResult(BaseModel):
    confirmation_id: UUID
    expires_at: BeijingTime
    folder_title: str
    existing_folder_id: str | None
    will_create_folder: bool
    privacy: FavoriteFolderPrivacy
    videos: list[LlmFavoriteSaveVideo]


class LlmFavoriteSaveTargetFolder(BaseModel):
    id: str
    title: str
    created: bool


class LlmFavoriteSaveResult(BaseModel):
    status: FavoriteSaveStatus
    folder: LlmFavoriteSaveTargetFolder
    videos: list[LlmFavoriteSaveVideo]
    retry_videos: list[LlmFavoriteSaveVideo]
    requested_count: int
    added_count: int | None
    upstream_code: int | None
    upstream_message: str | None
    retryable: bool


class LlmWatchLaterVideo(LlmSourceVideo):
    cid: str | None
    title: str
    duration_seconds: int
    progress_seconds: int
    published_at: BeijingTime
    author: LlmVideoAuthor
    stats: VideoStats


class LlmWatchLaterResult(BaseModel):
    videos: list[LlmWatchLaterVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class LlmHistoryVideo(LlmSourceVideo):
    cid: str | None
    title: str
    viewed_at: BeijingTime
    progress_seconds: int
    duration_seconds: int
    is_favorite: bool
    author: LlmVideoAuthor


class LlmHistoryResult(BaseModel):
    videos: list[LlmHistoryVideo]
    page_size: int
    has_more: bool
    next_max: int | None
    next_view_at: int | None


class LlmSearchVideo(LlmSourceVideo):
    aid: str
    title: str
    description: str
    duration_seconds: int
    published_at: BeijingTime
    author: LlmVideoAuthor
    stats: VideoStats


class LlmSearchVideoResult(BaseModel):
    videos: list[LlmSearchVideo]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class LlmFollowingVideo(LlmSourceVideo):
    cid: str | None
    title: str
    stats: FollowingVideoStats


class LlmFollowingVideoItem(BaseModel):
    dynamic_id: str
    published_at: BeijingTime
    author: LlmVideoAuthor
    video: LlmFollowingVideo
    dynamic_stats: FollowingDynamicStats


class LlmFollowingFeedResult(BaseModel):
    items: list[LlmFollowingVideoItem]
    has_more: bool
    next_offset: str | None


class LlmFollowingUser(BaseModel):
    mid: str
    name: str
    signature: str | None
    followed_at: BeijingTime | None
    is_mutual: bool | None
    is_special: bool | None
    official_verification: FollowingOfficialVerification | None


class LlmFollowingUsersResult(BaseModel):
    users: list[LlmFollowingUser]
    total: int
    page: int
    page_size: int
    has_more: bool


class LlmUserDynamicAuthor(BaseModel):
    mid: str | None
    name: str | None


class LlmUserDynamicContent(BaseModel):
    major_type: str | None
    title: str | None
    text: str | None
    bvid: str | None
    jump_url: str | None

    @computed_field
    @property
    def source_id(self) -> str | None:
        if self.bvid is None:
            return None
        return build_video_source_id(self.bvid)


class LlmUserDynamicItem(BaseModel):
    dynamic_id: str
    type: str | None
    published_at: BeijingTime | None
    is_pinned: bool | None
    author: LlmUserDynamicAuthor | None
    content: LlmUserDynamicContent
    stats: UserDynamicStats
    original: LlmUserDynamicItem | None


class LlmUserDynamicsResult(BaseModel):
    user_mid: str
    items: list[LlmUserDynamicItem]
    total_count: int
    skipped_count: int
    pages_fetched: int
    has_more: bool
    next_offset: str | None


class LlmIngestedDynamicContent(BaseModel):
    major_type: str | None
    title: str | None
    text: str | None
    bvid: str | None
    cid: str | None = None
    jump_url: str | None

    @computed_field
    @property
    def source_id(self) -> str | None:
        if self.bvid is None or self.cid is None:
            return None
        return build_video_source_id(self.bvid, self.cid)


class LlmIngestedDynamicItem(BaseModel):
    dynamic_id: str
    type: str | None
    published_at: BeijingTime | None
    is_pinned: bool | None
    author: LlmUserDynamicAuthor | None
    content: LlmIngestedDynamicContent
    stats: UserDynamicStats
    original: LlmIngestedDynamicItem | None


class LlmIngestedFavoriteResult(BaseModel):
    folder: LlmFavoriteFolder
    videos: list[VideoRecord]
    page: int
    page_size: int
    has_more: bool
    status: str
    skipped_count: int


class LlmIngestedPagedResult(BaseModel):
    videos: list[VideoRecord]
    total_count: int
    page: int
    page_size: int
    has_more: bool
    status: str
    skipped_count: int


class LlmIngestedHistoryResult(BaseModel):
    videos: list[VideoRecord]
    page_size: int
    has_more: bool
    next_max: int | None
    next_view_at: int | None
    status: str
    skipped_count: int


class LlmIngestedFollowingResult(BaseModel):
    videos: list[VideoRecord]
    has_more: bool
    next_offset: str | None
    status: str
    skipped_count: int


class LlmIngestedUserDynamicsResult(BaseModel):
    user_mid: str
    items: list[LlmIngestedDynamicItem]
    videos: list[VideoRecord]
    total_count: int
    skipped_count: int
    ingestion_skipped_count: int
    pages_fetched: int
    has_more: bool
    next_offset: str | None
    status: str


def _dynamic_item_with_cid(
    item: BaseModel,
    cid_by_bvid: dict[str, str],
) -> dict[str, object]:
    payload = item.model_dump(mode="python")
    content = payload.get("content")
    if isinstance(content, dict):
        bvid = content.get("bvid")
        content["cid"] = cid_by_bvid.get(bvid) if isinstance(bvid, str) else None
    original = getattr(item, "original", None)
    if isinstance(original, BaseModel):
        payload["original"] = _dynamic_item_with_cid(original, cid_by_bvid)
    return payload


def _project(result: BaseModel, model: type[BaseModel]) -> BaseModel:
    return model.model_validate(result.model_dump(mode="python"))


def project_favorite_folders(result: BaseModel) -> BaseModel:
    return _project(result, LlmFavoriteFolderListResult)


def project_favorite_folder_videos(result: BaseModel) -> BaseModel:
    if getattr(result, "ingestion_applied", False):
        return LlmIngestedFavoriteResult.model_validate(
            {
                "folder": result.folder.model_dump(mode="python"),
                "videos": result.video_records,
                "page": result.page,
                "page_size": result.page_size,
                "has_more": result.has_more,
                "status": result.ingestion_status,
                "skipped_count": result.ingestion_skipped_count,
            }
        )
    return _project(result, LlmFavoriteFolderVideosResult)


def project_favorite_save_preview(result: BaseModel) -> BaseModel:
    return _project(result, LlmFavoriteSavePreviewResult)


def project_favorite_save(result: BaseModel) -> BaseModel:
    return _project(result, LlmFavoriteSaveResult)


def project_watch_later(result: BaseModel) -> BaseModel:
    if getattr(result, "ingestion_applied", False):
        return LlmIngestedPagedResult.model_validate(
            {
                "videos": result.video_records,
                "total_count": result.total_count,
                "page": result.page,
                "page_size": result.page_size,
                "has_more": result.has_more,
                "status": result.ingestion_status,
                "skipped_count": result.ingestion_skipped_count,
            }
        )
    return _project(result, LlmWatchLaterResult)


def project_watch_history(result: BaseModel) -> BaseModel:
    if not isinstance(result, HistoryResponse):
        raise TypeError("project_watch_history 收到了错误的结果模型")
    if result.ingestion_applied:
        return LlmIngestedHistoryResult(
            videos=result.video_records,
            page_size=result.page_size,
            has_more=result.has_more,
            next_max=result.next_max,
            next_view_at=result.next_view_at,
            status=result.ingestion_status,
            skipped_count=result.ingestion_skipped_count,
        )
    return _project(result, LlmHistoryResult)


def project_search_videos(result: BaseModel) -> BaseModel:
    if getattr(result, "ingestion_applied", False):
        return LlmIngestedPagedResult.model_validate(
            {
                "videos": result.video_records,
                "total_count": result.total_count,
                "page": result.page,
                "page_size": result.page_size,
                "has_more": result.has_more,
                "status": result.ingestion_status,
                "skipped_count": result.ingestion_skipped_count,
            }
        )
    return _project(result, LlmSearchVideoResult)


def project_following_feed(result: BaseModel) -> BaseModel:
    if getattr(result, "ingestion_applied", False):
        return LlmIngestedFollowingResult.model_validate(
            {
                "videos": result.video_records,
                "has_more": result.has_more,
                "next_offset": result.next_offset,
                "status": result.ingestion_status,
                "skipped_count": result.ingestion_skipped_count,
            }
        )
    return _project(result, LlmFollowingFeedResult)


def project_following_users(result: BaseModel) -> BaseModel:
    return _project(result, LlmFollowingUsersResult)


def project_user_dynamics(result: BaseModel) -> BaseModel:
    if getattr(result, "ingestion_applied", False):
        cid_by_bvid = {
            record.identity.bvid: record.identity.cid
            for record in result.video_records
        }
        return LlmIngestedUserDynamicsResult.model_validate(
            {
                "user_mid": result.user_mid,
                "items": [
                    _dynamic_item_with_cid(item, cid_by_bvid)
                    for item in result.items
                ],
                "videos": result.video_records,
                "total_count": result.total_count,
                "skipped_count": result.skipped_count,
                "ingestion_skipped_count": result.ingestion_skipped_count,
                "pages_fetched": result.pages_fetched,
                "has_more": result.has_more,
                "next_offset": result.next_offset,
                "status": result.ingestion_status,
            }
        )
    return _project(result, LlmUserDynamicsResult)
