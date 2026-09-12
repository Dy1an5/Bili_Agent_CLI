from __future__ import annotations

import asyncio
import unittest

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.following import fetch_following_feed
from bili_agent_cli.schemas.following import FollowingFeedQuery, FollowingFeedResponse


FOLLOWING_PAYLOAD = {
    "code": 0,
    "message": "0",
    "ttl": 1,
    "data": {
        "has_more": True,
        "items": [
            {
                "id_str": "123",
                "type": "DYNAMIC_TYPE_AV",
                "modules": {
                    "module_author": {
                        "mid": 456,
                        "name": "测试UP主",
                        "face": "//i0.hdslb.com/avatar.jpg",
                        "pub_ts": 1_757_472_400,
                    },
                    "module_dynamic": {
                        "major": {
                            "type": "MAJOR_TYPE_ARCHIVE",
                            "archive": {
                                "bvid": "BV1test",
                                "title": "测试视频",
                                "cover": "http://i0.hdslb.com/cover.jpg",
                                "stat": {
                                    "play": "1.2万",
                                    "danmaku": "345",
                                },
                            },
                        }
                    },
                    "module_stat": {
                        "like": {"count": 101, "status": False},
                        "comment": {"count": 20, "status": False},
                        "forward": {"count": 3, "status": False},
                        "favorite": {"count": 9, "status": False},
                    },
                },
            }
        ],
        "offset": "next-offset",
        "update_baseline": "baseline",
        "update_num": 0,
        "unknown_data_field": [1, 2, 3],
    },
    "unknown_top_level_field": "keep-me",
}

EXPECTED_FOLLOWING_RESPONSE = {
    "items": [
        {
            "dynamic_id": "123",
            "published_at": "2025-09-10T02:46:40Z",
            "author": {
                "mid": "456",
                "name": "测试UP主",
                "avatar_url": "https://i0.hdslb.com/avatar.jpg",
            },
            "video": {
                "bvid": "BV1test",
                "cid": None,
                "title": "测试视频",
                "cover_url": "https://i0.hdslb.com/cover.jpg",
                "stats": {
                    "views": "1.2万",
                    "danmaku": "345",
                },
            },
            "dynamic_stats": {
                "likes": 101,
                "replies": 20,
                "reposts": 3,
                "favorites": 9,
            },
        }
    ],
    "has_more": True,
    "next_offset": "next-offset",
}


class FollowingModelTest(unittest.TestCase):
    def test_response_model_contract(self) -> None:
        response = FollowingFeedResponse.model_validate(
            EXPECTED_FOLLOWING_RESPONSE
        )

        self.assertEqual(
            response.model_dump(mode="json"),
            EXPECTED_FOLLOWING_RESPONSE,
        )

    def test_query_rejects_empty_offset(self) -> None:
        with self.assertRaises(ValidationError):
            FollowingFeedQuery(offset="")

    def test_fetches_following_feed_with_expected_request(self) -> None:
        async def run_scenario() -> FollowingFeedResponse:
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    request.url.path,
                    "/x/polymer/web-dynamic/v1/feed/all",
                )
                self.assertEqual(request.url.params["offset"], "current-offset")
                self.assertEqual(request.url.params["type"], "all")
                self.assertEqual(request.headers["cookie"], "SESSDATA=test-value")
                return httpx.Response(200, json=FOLLOWING_PAYLOAD)

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_following_feed(
                    FollowingFeedQuery(offset="current-offset"),
                    "SESSDATA=test-value",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual(
            response.model_dump(mode="json"),
            EXPECTED_FOLLOWING_RESPONSE,
        )


if __name__ == "__main__":
    unittest.main()
