from __future__ import annotations

import asyncio
import unittest

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.watch_later import fetch_watch_later
from bili_agent_cli.bilibili.wbi import create_mixin_key, sign_wbi_parameters
from bili_agent_cli.schemas.watch_later import WatchLaterQuery


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

WATCH_LATER_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "count": 3,
        "list": [
            {
                "bvid": "BV1later",
                "cid": 789,
                "title": "稍后再看的视频",
                "pic": "//i0.hdslb.com/later.jpg",
                "duration": 300,
                "progress": -1,
                "pubdate": 1_757_472_400,
                "owner": {
                    "mid": 456,
                    "name": "测试UP主",
                    "face": "http://i0.hdslb.com/avatar.jpg",
                },
            }
        ],
    },
}


class WatchLaterTest(unittest.TestCase):
    def test_query_rejects_zero_page(self) -> None:
        with self.assertRaises(ValidationError):
            WatchLaterQuery(page=0)

    def test_wbi_signing_is_deterministic(self) -> None:
        mixin_key = create_mixin_key(
            "7cd084941338484aae1ad9425b84077c",
            "4932caff0ff746eab6f01bf08b70ac45",
        )
        parameters = sign_wbi_parameters(
            {"foo": "114", "bar": "514", "zab": "1919810"},
            mixin_key,
            timestamp=1_702_204_169,
        )

        self.assertEqual(mixin_key, "ea1db124af3c7062474693fa704f4ff8")
        self.assertEqual(parameters["wts"], "1702204169")
        self.assertEqual(
            parameters["w_rid"],
            "8f6f2b5b3d485fe1886cec6a0be8c5d4",
        )

    def test_fetches_signed_watch_later_page(self) -> None:
        async def run_scenario():
            requested_paths: list[str] = []

            def handle_request(request: httpx.Request) -> httpx.Response:
                requested_paths.append(request.url.path)
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")

                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)

                self.assertEqual(
                    request.url.path,
                    "/x/v2/history/toview/web",
                )
                self.assertEqual(request.url.params["pn"], "2")
                self.assertEqual(request.url.params["ps"], "1")
                self.assertEqual(request.url.params["asc"], "true")
                self.assertIn("wts", request.url.params)
                self.assertEqual(len(request.url.params["w_rid"]), 32)
                return httpx.Response(200, json=WATCH_LATER_PAYLOAD)

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                response = await fetch_watch_later(
                    WatchLaterQuery(page=2, page_size=1, ascending=True),
                    "SESSDATA=test",
                    client,
                )

            self.assertEqual(
                requested_paths,
                ["/x/web-interface/nav", "/x/v2/history/toview/web"],
            )
            return response

        response = asyncio.run(run_scenario())
        result = response.model_dump(mode="json")
        self.assertEqual(result["videos"][0]["bvid"], "BV1later")
        self.assertEqual(result["videos"][0]["cid"], "789")
        self.assertEqual(result["videos"][0]["progress_seconds"], -1)
        self.assertEqual(
            result["videos"][0]["published_at"],
            "2025-09-10T02:46:40Z",
        )
        self.assertEqual(result["total_count"], 3)
        self.assertTrue(result["has_more"])


if __name__ == "__main__":
    unittest.main()
