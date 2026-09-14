from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from bili_agent_cli.schemas.favorites import FavoriteFolder, FavoriteVideo
from bili_agent_cli.schemas.following_feed import FollowingVideoItem
from bili_agent_cli.schemas.history import HistoryVideo
from bili_agent_cli.schemas.search import SearchVideoItem, SearchVideoQuery
from bili_agent_cli.schemas.user_dynamics import UserDynamicItem
from bili_agent_cli.schemas.watch_later import WatchLaterVideo

from .models import (
    DynamicFeedback,
    DynamicVideoContext,
    FavoriteVideoContext,
    HistoryVideoContext,
    SearchVideoContext,
    VideoAuthor,
    VideoContexts,
    VideoDetail,
    VideoFeedback,
    VideoIdentity,
    VideoRecord,
    WatchLaterVideoContext,
)


def _observed_at(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def parse_count(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None

    text = value.strip().replace(",", "")
    multipliers = {"万": 10_000, "亿": 100_000_000}
    multiplier = 1
    if text and text[-1] in multipliers:
        multiplier = multipliers[text[-1]]
        text = text[:-1]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number < 0:
        return None
    return int(number * multiplier)


def normalize_favorite_video(
    video: FavoriteVideo,
    *,
    cid: str,
    folder: FavoriteFolder,
    observed_at: datetime | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=video.bvid, cid=cid),
        author=VideoAuthor(**video.author.model_dump()),
        detail=VideoDetail(
            title=video.title,
            description=video.description,
            cover_url=video.cover_url,
            duration_seconds=video.duration_seconds,
        ),
        feedback=VideoFeedback(**video.stats.model_dump()),
        contexts=VideoContexts(
            favorite=FavoriteVideoContext(
                folder_id=video.folder_id,
                folder_title=folder.title,
                favorited_at=video.favorited_at,
            )
        ),
        observed_at=_observed_at(observed_at),
    )


def normalize_watch_later_video(
    video: WatchLaterVideo,
    *,
    cid: str,
    observed_at: datetime | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=video.bvid, cid=cid),
        author=VideoAuthor(**video.author.model_dump()),
        detail=VideoDetail(
            title=video.title,
            cover_url=video.cover_url,
            duration_seconds=video.duration_seconds,
            published_at=video.published_at,
        ),
        feedback=VideoFeedback(**video.stats.model_dump()),
        contexts=VideoContexts(
            watch_later=WatchLaterVideoContext(
                progress_seconds=video.progress_seconds
            )
        ),
        observed_at=_observed_at(observed_at),
    )


def normalize_history_video(
    video: HistoryVideo,
    *,
    cid: str,
    observed_at: datetime | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=video.bvid, cid=cid),
        author=VideoAuthor(**video.author.model_dump()),
        detail=VideoDetail(
            title=video.title,
            cover_url=video.cover_url,
            duration_seconds=video.duration_seconds,
        ),
        contexts=VideoContexts(
            history=HistoryVideoContext(
                viewed_at=video.viewed_at,
                progress_seconds=video.progress_seconds,
                is_favorite=video.is_favorite,
            )
        ),
        observed_at=_observed_at(observed_at),
    )


def normalize_search_video(
    video: SearchVideoItem,
    *,
    cid: str,
    query: SearchVideoQuery,
    rank: int,
    observed_at: datetime | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=video.bvid, cid=cid),
        author=VideoAuthor(**video.author.model_dump()),
        detail=VideoDetail(
            aid=video.aid,
            title=video.title,
            description=video.description,
            cover_url=video.cover_url,
            duration_seconds=video.duration_seconds,
            published_at=video.published_at,
        ),
        feedback=VideoFeedback(**video.stats.model_dump()),
        contexts=VideoContexts(
            search=SearchVideoContext(
                keyword=query.keyword,
                page=query.page,
                rank=rank,
                order=query.order.value,
                duration=int(query.duration),
                tid=query.tid,
                published_after=query.published_after,
                published_before=query.published_before,
            )
        ),
        observed_at=_observed_at(observed_at),
    )


def normalize_following_video(
    item: FollowingVideoItem,
    *,
    cid: str,
    observed_at: datetime | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=item.video.bvid, cid=cid),
        author=VideoAuthor(**item.author.model_dump()),
        detail=VideoDetail(
            title=item.video.title,
            cover_url=item.video.cover_url,
            published_at=item.published_at,
        ),
        feedback=VideoFeedback(
            views=parse_count(item.video.stats.views),
            danmaku=parse_count(item.video.stats.danmaku),
        ),
        contexts=VideoContexts(
            dynamic=DynamicVideoContext(
                dynamic_id=item.dynamic_id,
                published_at=item.published_at,
                publisher=VideoAuthor(**item.author.model_dump()),
                feedback=DynamicFeedback(**item.dynamic_stats.model_dump()),
            )
        ),
        observed_at=_observed_at(observed_at),
    )


def normalize_user_dynamic_video(
    item: UserDynamicItem,
    *,
    cid: str,
    observed_at: datetime | None = None,
) -> VideoRecord | None:
    if item.content.bvid is None:
        return None

    author = VideoAuthor(
        mid=item.author.mid if item.author else None,
        name=item.author.name if item.author else None,
        avatar_url=item.author.avatar_url if item.author else None,
    )
    return VideoRecord(
        identity=VideoIdentity(bvid=item.content.bvid, cid=cid),
        author=author,
        detail=VideoDetail(
            title=item.content.title,
            description=item.content.text,
            cover_url=item.content.cover_url,
            published_at=item.published_at,
        ),
        contexts=VideoContexts(
            dynamic=DynamicVideoContext(
                dynamic_id=item.dynamic_id,
                published_at=item.published_at,
                publisher=author,
                is_pinned=item.is_pinned,
                feedback=DynamicFeedback(**item.stats.model_dump()),
            )
        ),
        observed_at=_observed_at(observed_at),
    )
