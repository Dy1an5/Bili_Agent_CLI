from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bili_agent_cli.content.normalizers import (
    normalize_favorite_video,
    normalize_following_video,
    normalize_history_video,
    normalize_search_video,
    normalize_user_dynamic_video,
    normalize_watch_later_video,
    parse_count,
)
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingAuthor,
    FollowingDynamicStats,
    FollowingVideo,
    FollowingVideoItem,
    FollowingVideoStats,
)
from bili_agent_cli.schemas.history import HistoryVideo, HistoryVideoAuthor
from bili_agent_cli.schemas.search import (
    SearchVideoAuthor,
    SearchVideoItem,
    SearchVideoQuery,
    SearchVideoStats,
)
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicAuthor,
    UserDynamicContent,
    UserDynamicItem,
    UserDynamicStats,
)
from bili_agent_cli.schemas.watch_later import (
    WatchLaterVideo,
    WatchLaterVideoAuthor,
)


NOW = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)


class UnifiedVideoModelTest(unittest.TestCase):
    def test_all_sources_share_the_same_top_level_shape(self) -> None:
        author = FavoriteVideoAuthor(mid="10", name="UP", avatar_url="https://a")
        favorite = normalize_favorite_video(
            FavoriteVideo(
                folder_id="20",
                bvid="BVfavorite",
                title="favorite",
                cover_url="https://c",
                duration_seconds=100,
                favorited_at=NOW,
                author=author,
                stats=VideoStats(views=10),
            ),
            cid="101",
            folder=FavoriteFolder(
                id="20", title="folder", cover_url=None, media_count=1
            ),
            observed_at=NOW,
        )
        watch_later = normalize_watch_later_video(
            WatchLaterVideo(
                bvid="BVwatch",
                cid="102",
                title="watch",
                cover_url="https://c",
                duration_seconds=100,
                progress_seconds=10,
                published_at=NOW,
                author=WatchLaterVideoAuthor(
                    mid="10", name="UP", avatar_url="https://a"
                ),
                stats=VideoStats(),
            ),
            cid="102",
            observed_at=NOW,
        )
        history = normalize_history_video(
            HistoryVideo(
                bvid="BVhistory",
                cid="103",
                title="history",
                viewed_at=NOW,
                progress_seconds=-1,
                duration_seconds=100,
                is_favorite=False,
                author=HistoryVideoAuthor(mid="10", name="UP"),
            ),
            cid="103",
            observed_at=NOW,
        )
        search = normalize_search_video(
            SearchVideoItem(
                aid="1",
                bvid="BVsearch",
                title="search",
                description="desc",
                cover_url="https://c",
                duration_seconds=100,
                published_at=NOW,
                author=SearchVideoAuthor(mid="10", name="UP", avatar_url="https://a"),
                stats=SearchVideoStats(),
            ),
            cid="104",
            query=SearchVideoQuery(keyword="Python"),
            rank=1,
            observed_at=NOW,
        )
        following = normalize_following_video(
            FollowingVideoItem(
                dynamic_id="30",
                published_at=NOW,
                author=FollowingAuthor(mid="10", name="UP", avatar_url="https://a"),
                video=FollowingVideo(
                    bvid="BVfollowing",
                    cid=None,
                    title="following",
                    cover_url="https://c",
                    stats=FollowingVideoStats(views="1.2万", danmaku="20"),
                ),
                dynamic_stats=FollowingDynamicStats(likes=3),
            ),
            cid="105",
            observed_at=NOW,
        )
        user_dynamic = normalize_user_dynamic_video(
            UserDynamicItem(
                dynamic_id="31",
                published_at=NOW,
                author=UserDynamicAuthor(mid="10", name="UP", avatar_url="https://a"),
                content=UserDynamicContent(
                    bvid="BVuserdynamic",
                    title="dynamic",
                    cover_url="https://c",
                ),
                stats=UserDynamicStats(replies=2),
            ),
            cid="106",
            observed_at=NOW,
        )

        records = [favorite, watch_later, history, search, following, user_dynamic]
        self.assertIsNotNone(user_dynamic)
        expected_keys = {
            "identity",
            "author",
            "detail",
            "feedback",
            "contexts",
            "observed_at",
            "source_id",
        }
        expected_context_keys = {
            "favorite",
            "watch_later",
            "dynamic",
            "history",
            "search",
        }
        for record in records:
            assert record is not None
            payload = record.model_dump(mode="json")
            self.assertEqual(set(payload), expected_keys)
            self.assertEqual(set(payload["contexts"]), expected_context_keys)
            self.assertIn(":part:", payload["source_id"])
            self.assertEqual(
                payload["observed_at"],
                "2026-09-14 12:00 (UTC+8)",
            )

        self.assertEqual(
            favorite.model_dump(mode="json")["contexts"]["favorite"][
                "favorited_at"
            ],
            "2026-09-14 12:00 (UTC+8)",
        )
        self.assertEqual(
            watch_later.model_dump(mode="json")["detail"]["published_at"],
            "2026-09-14 12:00 (UTC+8)",
        )
        self.assertEqual(
            history.model_dump(mode="json")["contexts"]["history"][
                "viewed_at"
            ],
            "2026-09-14 12:00 (UTC+8)",
        )
        self.assertEqual(
            following.model_dump(mode="json")["contexts"]["dynamic"][
                "published_at"
            ],
            "2026-09-14 12:00 (UTC+8)",
        )
        self.assertEqual(
            favorite.model_dump(mode="python")["observed_at"],
            NOW,
        )

        self.assertEqual(following.feedback.views, 12_000)
        self.assertIsNone(watch_later.contexts.favorite)
        self.assertEqual(favorite.contexts.favorite.folder_title, "folder")

    def test_compact_count_parser_normalizes_numbers(self) -> None:
        self.assertEqual(parse_count("1.2万"), 12_000)
        self.assertEqual(parse_count("3亿"), 300_000_000)
        self.assertEqual(parse_count("1,234"), 1_234)
        self.assertIsNone(parse_count("--"))


if __name__ == "__main__":
    unittest.main()
