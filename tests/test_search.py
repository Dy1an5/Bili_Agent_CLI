from __future__ import annotations

import asyncio
import unittest

import httpx

from bili_agent_cli.bilibili.search import SearchVideoError, search_videos
from bili_agent_cli.schemas.search import SearchVideoQuery


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

SEARCH_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "numResults": 21,
        "result": [
            {
                "aid": 123,
                "bvid": "BV1search",
                "title": '<em class="keyword">Python</em> &amp; FastAPI',
                "description": "视频&lt;简介&gt;",
                "pic": "http://i0.hdslb.com/search.jpg",
                "pubdate": 1_757_472_400,
                "duration": "04:32",
                "mid": 456,
                "author": "测试UP主",
                "upic": "//i0.hdslb.com/avatar.jpg",
                "play": 1000,
                "danmaku": 20,
                "favorite": 30,
                "review": 40,
                "like": 50,
            }
        ],
    },
}


class SearchVideoTest(unittest.TestCase):
    def test_searches_videos_with_signed_parameters(self) -> None:
        query = SearchVideoQuery.model_validate(
            {
                "keyword": "Python 教程",
                "page": 1,
                "page_size": 20,
                "order": "pubdate",
                "duration": 2,
                "tid": 36,
                "published_after": "2025-09-01T00:00:00+08:00",
                "published_before": "2025-09-30T23:59:59+08:00",
            }
        )

        async def run_scenario():
            requested_paths: list[str] = []

            def handle_request(request: httpx.Request) -> httpx.Response:
                requested_paths.append(request.url.path)
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")

                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)

                self.assertEqual(
                    request.url.path,
                    "/x/web-interface/wbi/search/type",
                )
                self.assertEqual(request.url.params["search_type"], "video")
                self.assertEqual(request.url.params["keyword"], "Python 教程")
                self.assertEqual(request.url.params["order"], "pubdate")
                self.assertEqual(request.url.params["duration"], "2")
                self.assertEqual(request.url.params["tids"], "36")
                self.assertEqual(
                    request.url.params["pubtime_begin_s"],
                    str(int(query.published_after.timestamp())),
                )
                self.assertIn("wts", request.url.params)
                self.assertEqual(len(request.url.params["w_rid"]), 32)
                self.assertEqual(
                    request.headers["origin"],
                    "https://search.bilibili.com",
                )
                self.assertIn("keyword=Python+%E6%95%99%E7%A8%8B", request.headers["referer"])
                return httpx.Response(200, json=SEARCH_PAYLOAD)

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                response = await search_videos(
                    query,
                    "SESSDATA=test",
                    client,
                )

            self.assertEqual(
                requested_paths,
                [
                    "/x/web-interface/nav",
                    "/x/web-interface/wbi/search/type",
                ],
            )
            return response

        response = asyncio.run(run_scenario())
        result = response.model_dump(mode="json")
        video = result["videos"][0]
        self.assertEqual(video["title"], "Python & FastAPI")
        self.assertEqual(video["description"], "视频<简介>")
        self.assertEqual(video["duration_seconds"], 272)
        self.assertEqual(video["published_at"], "2025-09-10T02:46:40Z")
        self.assertEqual(video["stats"]["views"], 1000)
        self.assertEqual(result["total_count"], 21)
        self.assertTrue(result["has_more"])

    def test_reports_gaia_risk_control(self) -> None:
        async def run_scenario() -> None:
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)

                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "message": "0",
                        "data": {"v_voucher": "challenge-token"},
                    },
                )

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                with self.assertRaisesRegex(SearchVideoError, "人机验证"):
                    await search_videos(
                        SearchVideoQuery(keyword="Python"),
                        "SESSDATA=test",
                        client,
                    )

        asyncio.run(run_scenario())


if __name__ == "__main__":
    unittest.main()
