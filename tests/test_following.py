from __future__ import annotations

import asyncio
import unittest

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.following_feed import fetch_following_feed
from bili_agent_cli.bilibili.following_users import (
    FollowingUsersError,
    fetch_following_users,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)
from bili_agent_cli.schemas.following_users import (
    FollowingUsersQuery,
)


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

FOLLOWING_USERS_PAYLOAD = {
    "code": 0,
    "message": "0",
    "ttl": 1,
    "data": {
        "list": [
            {
                "mid": 456,
                "attribute": 6,
                "mtime": 1_757_472_400,
                "special": 1,
                "uname": "测试UP主",
                "face": "//i0.hdslb.com/avatar.jpg",
                "sign": "测试签名",
                "official_verify": {
                    "type": 0,
                    "desc": "官方账号",
                },
                "unknown_user_field": "ignored",
            },
            {
                "mid": "789",
                "attribute": 2,
                "mtime": 0,
                "special": 0,
                "uname": "普通UP主",
                "face": "http://i0.hdslb.com/avatar2.jpg",
                "sign": "",
                "official_verify": {
                    "type": -1,
                    "desc": "",
                },
            },
        ],
        "total": 21,
        "re_version": 0,
    },
}

EXPECTED_FOLLOWING_USERS_RESPONSE = {
    "users": [
        {
            "mid": "456",
            "name": "测试UP主",
            "avatar_url": "https://i0.hdslb.com/avatar.jpg",
            "signature": "测试签名",
            "followed_at": "2025-09-10T02:46:40Z",
            "is_mutual": True,
            "is_special": True,
            "official_verification": {
                "type": 0,
                "description": "官方账号",
            },
        },
        {
            "mid": "789",
            "name": "普通UP主",
            "avatar_url": "https://i0.hdslb.com/avatar2.jpg",
            "signature": None,
            "followed_at": None,
            "is_mutual": False,
            "is_special": False,
            "official_verification": {
                "type": -1,
                "description": None,
            },
        },
    ],
    "total": 21,
    "page": 1,
    "page_size": 20,
    "has_more": True,
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


class FollowingUsersTest(unittest.TestCase):
    def test_query_contract_and_boundaries(self) -> None:
        query = FollowingUsersQuery()

        self.assertEqual(query.page, 1)
        self.assertEqual(query.page_size, 20)
        self.assertEqual(query.sort.value, "recent")

        for raw_query in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 51},
            {"sort": "unknown"},
            {"unknown": 1},
        ):
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(ValidationError):
                    FollowingUsersQuery.model_validate(raw_query)

    def test_fetches_following_users_with_expected_request(self) -> None:
        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.url.path, "/x/relation/followings")
                self.assertEqual(request.url.params["vmid"], "123")
                self.assertEqual(request.url.params["pn"], "1")
                self.assertEqual(request.url.params["ps"], "20")
                self.assertEqual(request.url.params["order"], "desc")
                self.assertEqual(request.url.params["order_type"], "attention")
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                self.assertIn("user-agent", request.headers)
                self.assertIn("referer", request.headers)
                return httpx.Response(200, json=FOLLOWING_USERS_PAYLOAD)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request),
            ) as client:
                return await fetch_following_users(
                    "123",
                    FollowingUsersQuery(sort="frequent"),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual(
            response.model_dump(mode="json"),
            EXPECTED_FOLLOWING_USERS_RESPONSE,
        )

    def test_empty_list_and_recent_sort(self) -> None:
        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.url.params["order_type"], "")
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"list": [], "total": 0}},
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request),
            ) as client:
                return await fetch_following_users(
                    "123",
                    FollowingUsersQuery(),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual(response.users, [])
        self.assertEqual(response.total, 0)
        self.assertFalse(response.has_more)

    def test_skips_malformed_item_without_breaking_pagination(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {"mid": 456, "uname": "有效UP主"},
                    {"mid": 789, "uname": ""},
                    "not-an-object",
                ],
                "total": 2,
            },
        }

        async def run_scenario():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=payload)
                ),
            ) as client:
                return await fetch_following_users(
                    "123",
                    FollowingUsersQuery(page_size=1),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual([user.mid for user in response.users], ["456"])
        self.assertTrue(response.has_more)

    def test_business_error_preserves_code(self) -> None:
        async def run_scenario():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"code": -101, "message": "账号未登录"},
                    )
                ),
            ) as client:
                return await fetch_following_users(
                    "123",
                    FollowingUsersQuery(),
                    "SESSDATA=test",
                    client,
                )

        with self.assertRaises(FollowingUsersError) as context:
            asyncio.run(run_scenario())

        self.assertEqual(context.exception.code, -101)
        self.assertIn("账号未登录", str(context.exception))

    def test_http_network_json_and_structure_errors(self) -> None:
        async def run_scenario(handler):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            ) as client:
                return await fetch_following_users(
                    "123",
                    FollowingUsersQuery(),
                    "SESSDATA=test",
                    client,
                )

        scenarios = (
            (
                lambda request: httpx.Response(503, json={"code": -1}),
                "HTTP 503",
            ),
            (
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("offline", request=request)
                ),
                "网络请求失败",
            ),
            (
                lambda request: httpx.Response(200, content=b"not-json"),
                "无法解析的 JSON",
            ),
            (
                lambda request: httpx.Response(200, json=[]),
                "返回格式不正确",
            ),
            (
                lambda request: httpx.Response(
                    200,
                    json={"code": 0, "data": {"list": []}},
                ),
                "成功响应格式不正确",
            ),
        )

        for handler, message in scenarios:
            with self.subTest(message=message):
                with self.assertRaisesRegex(FollowingUsersError, message):
                    asyncio.run(run_scenario(handler))


if __name__ == "__main__":
    unittest.main()
