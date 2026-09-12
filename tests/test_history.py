from __future__ import annotations

import asyncio
import unittest

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.history import HistoryError, fetch_watch_history
from bili_agent_cli.schemas.history import HistoryQuery


HISTORY_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "list": [
            {
                "title": "看过的视频",
                "cover": "//i0.hdslb.com/history.jpg",
                "author_name": "历史UP主",
                "author_mid": 456,
                "author_face": "http://i0.hdslb.com/avatar.jpg",
                "view_at": 1_757_472_400,
                "progress": -1,
                "duration": 300,
                "is_fav": 1,
                "history": {
                    "oid": 123,
                    "bvid": "BV1history",
                    "cid": 789,
                    "business": "archive",
                },
            }
        ]
    },
}


class HistoryTest(unittest.TestCase):
    def test_query_contract_and_boundaries(self) -> None:
        query = HistoryQuery()

        self.assertEqual(query.page_size, 20)
        self.assertEqual(query.max, 0)
        self.assertEqual(query.view_at, 0)

        for raw_query in (
            {"page_size": 0},
            {"page_size": 31},
            {"max": -1},
            {"view_at": -1},
            {"unknown": 1},
        ):
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(ValidationError):
                    HistoryQuery.model_validate(raw_query)

    def test_fetches_and_parses_history_page(self) -> None:
        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    request.url.path,
                    "/x/web-interface/history/cursor",
                )
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                self.assertEqual(request.url.params["type"], "archive")
                self.assertEqual(request.url.params["ps"], "10")
                self.assertEqual(request.url.params["max"], "999")
                self.assertEqual(request.url.params["view_at"], "888")
                return httpx.Response(200, json=HISTORY_PAYLOAD)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request),
            ) as client:
                return await fetch_watch_history(
                    HistoryQuery(page_size=10, max=999, view_at=888),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        result = response.model_dump(mode="json")
        video = result["videos"][0]

        self.assertEqual(video["bvid"], "BV1history")
        self.assertEqual(video["cid"], "789")
        self.assertEqual(video["cover_url"], "https://i0.hdslb.com/history.jpg")
        self.assertEqual(video["author"]["mid"], "456")
        self.assertEqual(
            video["author"]["avatar_url"],
            "https://i0.hdslb.com/avatar.jpg",
        )
        self.assertEqual(video["viewed_at"], "2025-09-10T02:46:40Z")
        self.assertEqual(video["progress_seconds"], -1)
        self.assertTrue(video["is_favorite"])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_max"], 123)
        self.assertEqual(result["next_view_at"], 1_757_472_400)

    def test_filters_non_video_records_but_keeps_cursor(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "title": "一场直播",
                        "view_at": 100,
                        "history": {
                            "oid": 200,
                            "business": "live",
                        },
                    }
                ]
            },
        }

        async def run_scenario():
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_watch_history(
                    HistoryQuery(),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())

        self.assertEqual(response.videos, [])
        self.assertTrue(response.has_more)
        self.assertEqual(response.next_max, 200)
        self.assertEqual(response.next_view_at, 100)

    def test_empty_or_repeated_cursor_stops_pagination(self) -> None:
        async def fetch(payload, query):
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_watch_history(
                    query,
                    "SESSDATA=test",
                    client,
                )

        empty = asyncio.run(
            fetch({"code": 0, "data": {"list": []}}, HistoryQuery())
        )
        repeated = asyncio.run(
            fetch(
                HISTORY_PAYLOAD,
                HistoryQuery(max=123, view_at=1_757_472_400),
            )
        )

        self.assertFalse(empty.has_more)
        self.assertIsNone(empty.next_max)
        self.assertFalse(repeated.has_more)
        self.assertIsNone(repeated.next_view_at)

    def test_business_error_is_exposed(self) -> None:
        async def run_scenario():
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"code": -101, "message": "账号未登录"},
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_watch_history(
                    HistoryQuery(),
                    "SESSDATA=test",
                    client,
                )

        with self.assertRaisesRegex(HistoryError, "-101"):
            asyncio.run(run_scenario())

    def test_http_network_and_json_errors_are_exposed(self) -> None:
        async def run_scenario(handler):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            ) as client:
                return await fetch_watch_history(
                    HistoryQuery(),
                    "SESSDATA=test",
                    client,
                )

        error_handlers = (
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
        )

        for handler, message in error_handlers:
            with self.subTest(message=message):
                with self.assertRaisesRegex(HistoryError, message):
                    asyncio.run(run_scenario(handler))


if __name__ == "__main__":
    unittest.main()
