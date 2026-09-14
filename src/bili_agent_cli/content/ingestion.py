from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from bili_agent_cli.schemas.favorites import FavoriteFolderVideosResponse
from bili_agent_cli.schemas.following_feed import FollowingFeedResponse
from bili_agent_cli.schemas.history import HistoryResponse
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse
from bili_agent_cli.schemas.user_dynamics import UserDynamicItem, UserDynamicsResponse
from bili_agent_cli.schemas.watch_later import WatchLaterResponse

from .cid_resolver import (
    CidCandidate,
    CidResolutionBatch,
    ResolvedCid,
    resolve_video_cids,
)
from .models import VideoAuthor, VideoDetail, VideoFeedback, VideoRecord
from .normalizers import (
    normalize_favorite_video,
    normalize_following_video,
    normalize_history_video,
    normalize_search_video,
    normalize_user_dynamic_video,
    normalize_watch_later_video,
)
from .store import ContentStore


SOURCE_FAVORITE = "favorite"
SOURCE_FOLLOWING_FEED = "following_feed"
SOURCE_HISTORY = "history"
SOURCE_SEARCH = "search"
SOURCE_USER_DYNAMICS = "user_dynamics"
SOURCE_WATCH_LATER = "watch_later"

content_store = ContentStore()
ItemT = TypeVar("ItemT")
ModelT = TypeVar("ModelT", bound=BaseModel)


def _merge_non_null(
    primary: BaseModel,
    fallback: BaseModel,
    model: type[ModelT],
) -> ModelT:
    fallback_values = fallback.model_dump()
    primary_values = primary.model_dump()
    fallback_values.update(
        {key: value for key, value in primary_values.items() if value is not None}
    )
    return model.model_validate(fallback_values)


def _merge_detail(record: VideoRecord, resolved: ResolvedCid) -> VideoRecord:
    fallback = resolved.detail_record
    author = record.author
    detail = record.detail
    feedback = record.feedback
    if fallback is not None:
        author = _merge_non_null(record.author, fallback.author, VideoAuthor)
        detail = _merge_non_null(record.detail, fallback.detail, VideoDetail)
        feedback = _merge_non_null(
            record.feedback, fallback.feedback, VideoFeedback
        )
    if resolved.is_default is not None:
        detail = detail.model_copy(
            update={"is_default_part": resolved.is_default}
        )
    return record.model_copy(
        update={"author": author, "detail": detail, "feedback": feedback}
    )


async def _normalize_batch(
    items: Sequence[ItemT],
    *,
    sessdata_cookie: str,
    key_of: Callable[[int, ItemT], str],
    bvid_of: Callable[[ItemT], str],
    cid_of: Callable[[ItemT], str | None],
    normalize: Callable[[int, ItemT, str], VideoRecord | None],
    store: ContentStore,
) -> tuple[list[VideoRecord], CidResolutionBatch]:
    candidates = [
        CidCandidate(
            key=key_of(index, item),
            bvid=bvid_of(item),
            cid=cid_of(item),
        )
        for index, item in enumerate(items)
    ]
    resolution = await resolve_video_cids(
        candidates,
        sessdata_cookie=sessdata_cookie,
        lookup_default_cid=store.get_default_cid,
    )
    item_by_key = {
        key_of(index, item): (index, item)
        for index, item in enumerate(items)
    }
    records: list[VideoRecord] = []
    for resolved in resolution.resolved:
        index, item = item_by_key[resolved.key]
        normalized = normalize(index, item, resolved.cid)
        if normalized is not None:
            records.append(_merge_detail(normalized, resolved))
    return records, resolution


async def ingest_favorite_videos(
    response: FavoriteFolderVideosResponse,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> FavoriteFolderVideosResponse:
    store = store or content_store
    records, resolution = await _normalize_batch(
        response.videos,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, video: (
            f"folder:{response.folder.id}:page:{response.page}:item:{index}"
        ),
        bvid_of=lambda video: video.bvid,
        cid_of=lambda video: None,
        normalize=lambda _index, video, cid: normalize_favorite_video(
            video, cid=cid, folder=response.folder
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_FAVORITE,
        source_scope=response.folder.id,
        failures=resolution.failures,
        folder=response.folder,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )


async def ingest_watch_later(
    response: WatchLaterResponse,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> WatchLaterResponse:
    store = store or content_store
    records, resolution = await _normalize_batch(
        response.videos,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, _video: f"page:{response.page}:item:{index}",
        bvid_of=lambda video: video.bvid,
        cid_of=lambda video: video.cid,
        normalize=lambda _index, video, cid: normalize_watch_later_video(
            video, cid=cid
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_WATCH_LATER,
        failures=resolution.failures,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )


async def ingest_history(
    response: HistoryResponse,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> HistoryResponse:
    store = store or content_store
    records, resolution = await _normalize_batch(
        response.videos,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, video: f"viewed:{video.viewed_at.isoformat()}:{index}",
        bvid_of=lambda video: video.bvid,
        cid_of=lambda video: video.cid,
        normalize=lambda _index, video, cid: normalize_history_video(
            video, cid=cid
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_HISTORY,
        failures=resolution.failures,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )


async def ingest_search(
    response: SearchVideoResponse,
    query: SearchVideoQuery,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> SearchVideoResponse:
    store = store or content_store
    records, resolution = await _normalize_batch(
        response.videos,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, _video: f"page:{query.page}:item:{index}",
        bvid_of=lambda video: video.bvid,
        cid_of=lambda video: None,
        normalize=lambda index, video, cid: normalize_search_video(
            video, cid=cid, query=query, rank=index + 1
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_SEARCH,
        source_scope=query.keyword,
        failures=resolution.failures,
        search_query=query,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )


async def ingest_following_feed(
    response: FollowingFeedResponse,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> FollowingFeedResponse:
    store = store or content_store
    records, resolution = await _normalize_batch(
        response.items,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, item: f"dynamic:{item.dynamic_id}:{index}",
        bvid_of=lambda item: item.video.bvid,
        cid_of=lambda item: item.video.cid,
        normalize=lambda _index, item, cid: normalize_following_video(
            item, cid=cid
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_FOLLOWING_FEED,
        source_scope="following",
        failures=resolution.failures,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )


def _walk_dynamic_items(
    items: Sequence[UserDynamicItem],
) -> list[UserDynamicItem]:
    result: list[UserDynamicItem] = []

    def visit(item: UserDynamicItem, depth: int) -> None:
        if depth > 8:
            return
        if item.content.bvid is not None:
            result.append(item)
        if item.original is not None:
            visit(item.original, depth + 1)

    for item in items:
        visit(item, 0)
    return result


async def ingest_user_dynamics(
    response: UserDynamicsResponse,
    *,
    sessdata_cookie: str,
    store: ContentStore | None = None,
) -> UserDynamicsResponse:
    store = store or content_store
    items = _walk_dynamic_items(response.items)
    records, resolution = await _normalize_batch(
        items,
        sessdata_cookie=sessdata_cookie,
        key_of=lambda index, item: f"dynamic:{item.dynamic_id}:{index}",
        bvid_of=lambda item: item.content.bvid or "",
        cid_of=lambda _item: None,
        normalize=lambda _index, item, cid: normalize_user_dynamic_video(
            item, cid=cid
        ),
        store=store,
    )
    store.save_batch(
        records,
        source=SOURCE_USER_DYNAMICS,
        source_scope=response.user_mid,
        failures=resolution.failures,
    )
    return response.model_copy(
        update={
            "video_records": records,
            "ingestion_applied": True,
            "ingestion_status": resolution.status,
            "ingestion_skipped_count": resolution.skipped_count,
        }
    )
