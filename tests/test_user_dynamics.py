from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bili_agent_cli.bilibili.user_dynamics import (
    UserDynamicsError,
    fetch_all_user_dynamics,
    fetch_user_dynamics_page,
)
from bili_agent_cli.schemas.user_dynamics import UserDynamicsResponse


NAV_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "wbi_img": {
            "img_url": (
                "https://i0.hdslb.com/bfs/wbi/"
                "abcdefghijklmnopqrstuvwxyz0123456789.png"
            ),
            "sub_url": (
                "https://i0.hdslb.com/bfs/wbi/"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.png"
            ),
        }
    },
}


def _author(mid: int = 456) -> dict[str, object]:
    return {
        "mid": mid,
        "name": "测试UP主",
        "face": "//i0.hdslb.com/avatar.jpg",
        "pub_ts": 1_757_472_400,
        "is_top": False,
    }


def _video_dynamic(dynamic_id: str = "101") -> dict[str, object]:
    return {
        "id_str": dynamic_id,
        "type": "DYNAMIC_TYPE_AV",
        "visible": True,
        "modules": {
            "module_author": _author(),
            "module_dynamic": {
                "desc": {"text": "视频动态正文"},
                "major": {
                    "type": "MAJOR_TYPE_ARCHIVE",
                    "archive": {
                        "bvid": "BV1dynamic",
                        "title": "动态视频",
                        "cover": "http://i0.hdslb.com/video.jpg",
                        "jump_url": "//www.bilibili.com/video/BV1dynamic",
                    },
                },
            },
            "module_stat": {
                "like": {"count": 10},
                "comment": {"count": "2"},
                "forward": {"count": 1},
                "favorite": {"count": 3},
            },
        },
    }


def _opus_dynamic(dynamic_id: str = "102") -> dict[str, object]:
    return {
        "id_str": dynamic_id,
        "type": "DYNAMIC_TYPE_DRAW",
        "modules": {
            "module_author": _author(),
            "module_dynamic": {
                "major": {
                    "type": "MAJOR_TYPE_OPUS",
                    "opus": {
                        "title": "图文标题",
                        "summary": {"text": "图文正文"},
                        "pics": [
                            {"url": "//i0.hdslb.com/one.jpg"},
                            {"src": "http://i0.hdslb.com/two.jpg"},
                        ],
                    },
                }
            },
        },
    }


class UserDynamicsClientTest(unittest.TestCase):
    def test_fetches_exactly_one_page_and_returns_next_offset(self) -> None:
        requested_offsets: list[str] = []

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)
                self.assertEqual(
                    request.url.path,
                    "/x/polymer/web-dynamic/v1/feed/space",
                )
                requested_offsets.append(request.url.params["offset"])
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "items": [_video_dynamic()],
                            "has_more": True,
                            "offset": "next-cursor",
                        },
                    },
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await fetch_user_dynamics_page(
                    "456",
                    "current-cursor",
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())

        self.assertEqual(requested_offsets, ["current-cursor"])
        self.assertEqual(response.pages_fetched, 1)
        self.assertTrue(response.has_more)
        self.assertEqual(response.next_offset, "next-cursor")
        self.assertEqual(response.total_count, 1)

    def test_fetches_all_pages_with_wbi_and_parses_dynamic_types(self) -> None:
        forwarded = {
            "id_str": "103",
            "type": "DYNAMIC_TYPE_FORWARD",
            "modules": {
                "module_author": _author(),
                "module_dynamic": {"desc": {"text": "转发理由"}},
            },
            "orig": _opus_dynamic("99"),
        }
        pages = [
            {
                "code": 0,
                "data": {
                    "items": [_video_dynamic()],
                    "has_more": True,
                    "offset": "next-cursor",
                },
            },
            {
                "code": 0,
                "data": {
                    "items": [_opus_dynamic(), forwarded],
                    "has_more": False,
                    "offset": "",
                },
            },
        ]

        async def run_scenario() -> UserDynamicsResponse:
            requested_offsets: list[str] = []

            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)

                self.assertEqual(
                    request.url.path,
                    "/x/polymer/web-dynamic/v1/feed/space",
                )
                self.assertEqual(request.url.params["host_mid"], "456")
                self.assertEqual(request.url.params["timezone_offset"], "-480")
                self.assertEqual(request.url.params["platform"], "web")
                self.assertEqual(request.url.params["web_location"], "333.1387")
                self.assertIn("itemOpusStyle", request.url.params["features"])
                self.assertEqual(request.url.params["dm_img_list"], "[]")
                self.assertTrue(request.url.params["dm_img_str"])
                self.assertTrue(request.url.params["dm_cover_img_str"])
                self.assertIn("platform", request.url.params["x-bili-device-req-json"])
                self.assertIn("wts", request.url.params)
                self.assertEqual(len(request.url.params["w_rid"]), 32)
                self.assertEqual(
                    request.headers["referer"],
                    "https://space.bilibili.com/456/dynamic",
                )
                requested_offsets.append(request.url.params["offset"])
                return httpx.Response(200, json=pages[len(requested_offsets) - 1])

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request),
            ) as client:
                response = await fetch_all_user_dynamics(
                    "456",
                    "SESSDATA=test",
                    client,
                )

            self.assertEqual(requested_offsets, ["", "next-cursor"])
            return response

        response = asyncio.run(run_scenario())
        self.assertEqual(response.user_mid, "456")
        self.assertEqual(response.total_count, 3)
        self.assertEqual(response.skipped_count, 0)
        self.assertEqual(response.pages_fetched, 2)

        video = response.items[0]
        self.assertEqual(video.dynamic_id, "101")
        self.assertEqual(video.published_at.isoformat(), "2025-09-10T02:46:40+00:00")
        self.assertEqual(video.content.bvid, "BV1dynamic")
        self.assertEqual(video.content.cover_url, "https://i0.hdslb.com/video.jpg")
        self.assertEqual(video.stats.replies, 2)

        opus = response.items[1]
        self.assertEqual(opus.content.text, "图文正文")
        self.assertEqual(
            opus.content.image_urls,
            [
                "https://i0.hdslb.com/one.jpg",
                "https://i0.hdslb.com/two.jpg",
            ],
        )

        repost = response.items[2]
        self.assertEqual(repost.content.text, "转发理由")
        self.assertIsNotNone(repost.original)
        self.assertEqual(repost.original.dynamic_id, "99")
        self.assertEqual(repost.original.content.title, "图文标题")

    def test_empty_page_and_malformed_items(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "items": ["bad", {"id_str": "0"}, _video_dynamic("101")],
                "has_more": False,
                "offset": None,
            },
        }

        response = asyncio.run(self._run_with_page(payload))
        self.assertEqual(response.total_count, 1)
        self.assertEqual(response.skipped_count, 2)
        self.assertEqual(response.pages_fetched, 1)

        empty = asyncio.run(
            self._run_with_page(
                {
                    "code": 0,
                    "data": {"items": [], "has_more": False, "offset": ""},
                }
            )
        )
        self.assertEqual(empty.items, [])
        self.assertEqual(empty.total_count, 0)

    def test_business_error_preserves_code(self) -> None:
        with self.assertRaises(UserDynamicsError) as context:
            asyncio.run(
                self._run_with_page(
                    {"code": -404, "message": "用户不存在"}
                )
            )

        self.assertEqual(context.exception.code, -404)
        self.assertIn("用户不存在", str(context.exception))

    def test_wbi_http_error_preserves_status(self) -> None:
        async def run_scenario() -> UserDynamicsResponse:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(503, json={"code": -1})
                ),
            ) as client:
                return await fetch_all_user_dynamics(
                    "456",
                    "SESSDATA=test",
                    client,
                )

        with self.assertRaises(UserDynamicsError) as context:
            asyncio.run(run_scenario())

        self.assertEqual(context.exception.status, 503)
        self.assertIn("HTTP 503", str(context.exception))

    def test_http_network_json_and_structure_errors(self) -> None:
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
                    json={"code": 0, "data": {"items": []}},
                ),
                "成功响应格式不正确",
            ),
        )

        for page_handler, message in scenarios:
            with self.subTest(message=message):
                with self.assertRaisesRegex(UserDynamicsError, message):
                    asyncio.run(self._run_with_handler(page_handler))

    def test_rejects_missing_and_repeated_pagination_cursor(self) -> None:
        missing_cursor = {
            "code": 0,
            "data": {"items": [], "has_more": True, "offset": ""},
        }
        with self.assertRaisesRegex(UserDynamicsError, "游标缺失"):
            asyncio.run(self._run_with_page(missing_cursor))

        repeated_cursor = {
            "code": 0,
            "data": {"items": [], "has_more": True, "offset": "same"},
        }
        calls = 0

        def handle_page(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=repeated_cursor)

        with self.assertRaisesRegex(UserDynamicsError, "游标重复"):
            asyncio.run(self._run_with_handler(handle_page))
        self.assertEqual(calls, 2)

    async def _run_with_page(
        self,
        payload: object,
    ) -> UserDynamicsResponse:
        return await self._run_with_handler(
            lambda request: httpx.Response(200, json=payload)
        )

    async def _run_with_handler(self, page_handler) -> UserDynamicsResponse:
        def handle_request(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/web-interface/nav":
                return httpx.Response(200, json=NAV_PAYLOAD)
            return page_handler(request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle_request),
        ) as client:
            return await fetch_all_user_dynamics(
                "456",
                "SESSDATA=test",
                client,
            )


if __name__ == "__main__":
    unittest.main()
