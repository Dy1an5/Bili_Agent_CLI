from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from bili_agent_cli.agent.result_models import (
    project_favorite_folder_videos,
    project_following_feed,
    project_search_videos,
    project_user_dynamics,
    project_watch_history,
    project_watch_later,
)
from bili_agent_cli.content.cid_resolver import CidResolutionBatch, ResolvedCid
from bili_agent_cli.content.ingestion import (
    ingest_favorite_videos,
    ingest_following_feed,
    ingest_history,
    ingest_search,
    ingest_user_dynamics,
    ingest_watch_later,
)
from bili_agent_cli.content.store import ContentStore
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingAuthor,
    FollowingFeedResponse,
    FollowingVideo,
    FollowingVideoItem,
)
from bili_agent_cli.schemas.history import (
    HistoryResponse,
    HistoryVideo,
    HistoryVideoAuthor,
)
from bili_agent_cli.schemas.search import (
    SearchVideoAuthor,
    SearchVideoItem,
    SearchVideoQuery,
    SearchVideoResponse,
    SearchVideoStats,
)
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicAuthor,
    UserDynamicContent,
    UserDynamicItem,
    UserDynamicsResponse,
)
from bili_agent_cli.schemas.watch_later import (
    WatchLaterResponse,
    WatchLaterVideo,
    WatchLaterVideoAuthor,
)


NOW = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


async def resolve_for_test(candidates, **_kwargs) -> CidResolutionBatch:
    return CidResolutionBatch(
        resolved=[
            ResolvedCid(
                key=candidate.key,
                bvid=candidate.bvid,
                cid=candidate.cid or str(1000 + index),
                is_default=candidate.cid is None,
            )
            for index, candidate in enumerate(candidates)
        ],
        failures=[],
    )


def row_count(store: ContentStore, table: str) -> int:
    with store.connect() as connection:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_all_six_video_reads_normalize_store_and_project_one_shape(
    tmp_path,
) -> None:
    store = ContentStore(tmp_path / "content.db")
    folder = FavoriteFolder(
        id="10", title="收藏", cover_url=None, media_count=1
    )
    favorite = FavoriteFolderVideosResponse(
        folder=folder,
        videos=[
            FavoriteVideo(
                folder_id="10",
                bvid="BV1favorite",
                title="收藏视频",
                cover_url="https://example.test/f.jpg",
                duration_seconds=60,
                favorited_at=NOW,
                author=FavoriteVideoAuthor(
                    mid="1", name="UP", avatar_url="https://example.test/a.jpg"
                ),
            )
        ],
        page=1,
        page_size=20,
        has_more=False,
    )
    watch_later = WatchLaterResponse(
        videos=[
            WatchLaterVideo(
                bvid="BV1later",
                cid="2001",
                title="稍后再看",
                cover_url="https://example.test/l.jpg",
                duration_seconds=80,
                progress_seconds=12,
                published_at=NOW,
                author=WatchLaterVideoAuthor(
                    mid="2", name="UP", avatar_url="https://example.test/a.jpg"
                ),
            )
        ],
        total_count=1,
        page=1,
        page_size=20,
        has_more=False,
    )
    history = HistoryResponse(
        videos=[
            HistoryVideo(
                bvid="BV1history",
                cid="3001",
                title="历史视频",
                viewed_at=NOW,
                progress_seconds=20,
                duration_seconds=100,
                is_favorite=False,
                author=HistoryVideoAuthor(mid="3", name="UP"),
            )
        ],
        page_size=20,
        has_more=False,
    )
    query = SearchVideoQuery(keyword="Agent")
    search = SearchVideoResponse(
        videos=[
            SearchVideoItem(
                aid="4",
                bvid="BV1search",
                title="搜索视频",
                description="简介",
                cover_url="https://example.test/s.jpg",
                duration_seconds=120,
                published_at=NOW,
                author=SearchVideoAuthor(
                    mid="4", name="UP", avatar_url="https://example.test/a.jpg"
                ),
                stats=SearchVideoStats(views=0),
            )
        ],
        total_count=1,
        page=1,
        page_size=20,
        has_more=False,
    )
    following = FollowingFeedResponse(
        items=[
            FollowingVideoItem(
                dynamic_id="50",
                published_at=NOW,
                author=FollowingAuthor(
                    mid="5", name="UP", avatar_url="https://example.test/a.jpg"
                ),
                video=FollowingVideo(
                    bvid="BV1following",
                    cid="5001",
                    title="关注视频",
                    cover_url="https://example.test/d.jpg",
                ),
            )
        ],
        has_more=False,
        next_offset=None,
    )
    dynamics = UserDynamicsResponse(
        user_mid="6",
        items=[
            UserDynamicItem(
                dynamic_id="60",
                published_at=NOW,
                author=UserDynamicAuthor(mid="6", name="UP"),
                content=UserDynamicContent(
                    bvid="BV1dynamic", title="用户动态视频"
                ),
            )
        ],
        total_count=1,
        skipped_count=0,
        pages_fetched=1,
    )

    async def ingest_all():
        return [
            await ingest_favorite_videos(
                favorite, sessdata_cookie="SESSDATA=x", store=store
            ),
            await ingest_watch_later(
                watch_later, sessdata_cookie="SESSDATA=x", store=store
            ),
            await ingest_history(
                history, sessdata_cookie="SESSDATA=x", store=store
            ),
            await ingest_search(
                search, query, sessdata_cookie="SESSDATA=x", store=store
            ),
            await ingest_following_feed(
                following, sessdata_cookie="SESSDATA=x", store=store
            ),
            await ingest_user_dynamics(
                dynamics, sessdata_cookie="SESSDATA=x", store=store
            ),
        ]

    with patch(
        "bili_agent_cli.content.ingestion.resolve_video_cids",
        new=resolve_for_test,
    ):
        results = asyncio.run(ingest_all())

    projectors = [
        project_favorite_folder_videos,
        project_watch_later,
        project_watch_history,
        project_search_videos,
        project_following_feed,
        project_user_dynamics,
    ]
    video_shapes = []
    for result, projector in zip(results, projectors, strict=True):
        assert result.ingestion_applied is True
        assert result.ingestion_status == "completed"
        payload = projector(result).model_dump(mode="json")
        video_shapes.append(set(payload["videos"][0]))
        assert ":part:" in payload["videos"][0]["source_id"]

    assert all(shape == video_shapes[0] for shape in video_shapes)
    dynamics_payload = project_user_dynamics(results[-1]).model_dump(mode="json")
    assert dynamics_payload["items"][0]["content"]["source_id"] == (
        "bilibili:video:BV1dynamic:part:1000"
    )
    assert row_count(store, "videos") == 6
    assert row_count(store, "favorite_items") == 1
    assert row_count(store, "watch_later_items") == 1
    assert row_count(store, "history_items") == 1
    assert row_count(store, "search_results") == 1
    assert row_count(store, "dynamic_items") == 2
