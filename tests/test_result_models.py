from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from bili_agent_cli.agent.result_models import (
    LlmFavoriteFolderVideosResult,
    LlmFollowingFeedResult,
    LlmFollowingUsersResult,
    LlmSearchVideoResult,
    LlmVideoAuthor,
    LlmWatchLaterResult,
    format_beijing_time,
    project_following_users,
)
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.following_users import (
    FollowingOfficialVerification,
    FollowingUser,
    FollowingUsersResponse,
)


class FormatBeijingTimeTest(unittest.TestCase):
    def test_none_stays_none(self) -> None:
        self.assertIsNone(format_beijing_time(None))

    def test_converts_aware_utc_to_utc_plus_eight(self) -> None:
        self.assertEqual(
            format_beijing_time(
                datetime(2026, 9, 12, 3, 0, 2, tzinfo=timezone.utc)
            ),
            "2026-09-12 11:00 (UTC+8)",
        )

    def test_converts_other_offset_to_utc_plus_eight(self) -> None:
        self.assertEqual(
            format_beijing_time(
                datetime(
                    2026,
                    9,
                    12,
                    3,
                    0,
                    2,
                    tzinfo=timezone(timedelta(hours=-5)),
                )
            ),
            "2026-09-12 16:00 (UTC+8)",
        )


class FollowingFeedProjectionTest(unittest.TestCase):
    def _result(self) -> LlmFollowingFeedResult:
        return LlmFollowingFeedResult.model_validate(
            {
                "items": [
                    {
                        "dynamic_id": "1246759358846468117",
                        "published_at": "2026-09-12T03:00:02Z",
                        "author": {"mid": "1", "name": "发条饭团"},
                        "video": {
                            "bvid": "BV1W7bG6nEDR",
                            "cid": None,
                            "title": "初秋购物分享",
                            "stats": {"views": "409", "danmaku": "3"},
                        },
                        "dynamic_stats": {
                            "likes": 49,
                            "replies": 12,
                            "reposts": 0,
                            "favorites": None,
                        },
                    }
                ],
                "has_more": True,
                "next_offset": "1246759358846468117",
            }
        )

    def test_serializes_published_at_as_utc_plus_eight_text(self) -> None:
        payload = self._result().model_dump(mode="json")

        self.assertEqual(
            payload["items"][0]["published_at"],
            "2026-09-12 11:00 (UTC+8)",
        )

    def test_keeps_datetime_in_python_mode(self) -> None:
        item = self._result().items[0]

        self.assertEqual(item.published_at.year, 2026)
        self.assertIsNotNone(item.published_at.tzinfo)
        self.assertEqual(item.published_at.utcoffset().total_seconds(), 0)


class FollowingUsersProjectionTest(unittest.TestCase):
    def test_keeps_answer_fields_and_trims_avatar(self) -> None:
        full_result = FollowingUsersResponse(
            users=[
                FollowingUser(
                    mid="456",
                    name="测试UP主",
                    avatar_url="https://i0.hdslb.com/avatar.jpg",
                    signature="测试签名",
                    followed_at=1_757_472_400,
                    is_mutual=True,
                    is_special=False,
                    official_verification=FollowingOfficialVerification(
                        type=0,
                        description="官方账号",
                    ),
                )
            ],
            total=21,
            page=1,
            page_size=20,
            has_more=True,
        )

        projected = project_following_users(full_result)

        self.assertIsInstance(projected, LlmFollowingUsersResult)
        full_payload = full_result.model_dump(mode="json")
        payload = projected.model_dump(mode="json")
        self.assertIn("avatar_url", full_payload["users"][0])
        self.assertNotIn("avatar_url", payload["users"][0])
        self.assertEqual(payload["users"][0]["mid"], "456")
        self.assertEqual(payload["users"][0]["signature"], "测试签名")
        self.assertEqual(
            payload["users"][0]["followed_at"],
            "2025-09-10 10:46 (UTC+8)",
        )
        self.assertEqual(
            payload["users"][0]["official_verification"],
            {"type": 0, "description": "官方账号"},
        )
        self.assertEqual(payload["total"], 21)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 20)
        self.assertTrue(payload["has_more"])


class OptionalTimeProjectionTest(unittest.TestCase):
    def test_null_favorited_at_stays_null(self) -> None:
        result = LlmFavoriteFolderVideosResult.model_validate(
            {
                "folder": {"id": "1", "title": "默认收藏夹", "media_count": 1},
                "videos": [
                    {
                        "folder_id": "1",
                        "bvid": "BV1favorite",
                        "title": "收藏的视频",
                        "description": None,
                        "duration_seconds": 120,
                        "favorited_at": None,
                        "author": {"mid": "456", "name": "测试UP主"},
                        "stats": {"views": 1, "danmaku": 0},
                    }
                ],
                "page": 1,
                "page_size": 20,
                "has_more": False,
            }
        )

        self.assertIsNone(
            result.model_dump(mode="json")["videos"][0]["favorited_at"]
        )

    def test_naive_datetime_is_treated_as_utc(self) -> None:
        self.assertEqual(
            format_beijing_time(
                LlmWatchLaterResult.model_validate(
                    {
                        "videos": [
                            {
                                "bvid": "BV1later",
                                "cid": None,
                                "title": "稍后再看",
                                "duration_seconds": 60,
                                "progress_seconds": 10,
                                "published_at": "2025-09-10T02:46:40",
                                "author": {"mid": "1", "name": "测试UP主"},
                                "stats": {"views": 0, "danmaku": 0},
                            }
                        ],
                        "total_count": 1,
                        "page": 1,
                        "page_size": 20,
                        "has_more": False,
                    }
                ).videos[0].published_at
            ),
            "2025-09-10 10:46 (UTC+8)",
        )


class SearchProjectionTest(unittest.TestCase):
    def test_search_published_at_uses_utc_plus_eight(self) -> None:
        result = LlmSearchVideoResult(
            videos=[
                {
                    "aid": "1",
                    "bvid": "BV1search",
                    "title": "搜索结果",
                    "description": "",
                    "duration_seconds": 90,
                    "published_at": "2025-09-10T02:46:40Z",
                    "author": LlmVideoAuthor(mid="1", name="测试UP主"),
                    "stats": VideoStats(views=1, danmaku=0),
                }
            ],
            total_count=1,
            page=1,
            page_size=20,
            has_more=False,
        )

        payload = result.model_dump(mode="json")

        self.assertEqual(
            payload["videos"][0]["published_at"],
            "2025-09-10 10:46 (UTC+8)",
        )


class InvalidTimeTest(unittest.TestCase):
    def test_rejects_non_datetime_published_at(self) -> None:
        with self.assertRaises(ValidationError):
            LlmFollowingFeedResult.model_validate(
                {
                    "items": [
                        {
                            "dynamic_id": "1",
                            "published_at": "不是时间",
                            "author": {"mid": "1", "name": "测试UP主"},
                            "video": {
                                "bvid": "BV1bad",
                                "cid": None,
                                "title": "标题",
                                "stats": {"views": None, "danmaku": None},
                            },
                            "dynamic_stats": {
                                "likes": None,
                                "replies": None,
                                "reposts": None,
                                "favorites": None,
                            },
                        }
                    ],
                    "has_more": False,
                    "next_offset": None,
                }
            )


if __name__ == "__main__":
    unittest.main()
