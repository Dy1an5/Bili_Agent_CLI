from __future__ import annotations

import asyncio
import unittest

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.favorites import (
    fetch_favorite_folder_videos,
    fetch_favorite_folders,
)
from bili_agent_cli.schemas.favorites import FavoriteVideosQuery


FOLDER_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "count": 1,
        "list": [
            {
                "id": 1001,
                "title": "默认收藏夹",
                "cover": "//i0.hdslb.com/folder.jpg",
                "media_count": 2,
            }
        ],
    },
}

VIDEOS_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "info": {
            "id": 1001,
            "title": "默认收藏夹",
            "cover": "//i0.hdslb.com/folder.jpg",
            "media_count": 2,
        },
        "medias": [
            {
                "bvid": "BV1favorite",
                "title": "收藏的视频",
                "cover": "http://i0.hdslb.com/favorite.jpg",
                "duration": 125,
                "fav_time": 1_757_472_400,
                "upper": {
                    "mid": 456,
                    "name": "测试UP主",
                    "face": "//i0.hdslb.com/avatar.jpg",
                },
            }
        ],
        "has_more": True,
    },
}


class FavoritesTest(unittest.TestCase):
    def test_query_rejects_invalid_page_size(self) -> None:
        with self.assertRaises(ValidationError):
            FavoriteVideosQuery(page_size=41)

    def test_fetches_favorite_folders(self) -> None:
        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    request.url.path,
                    "/x/v3/fav/folder/created/list-all",
                )
                self.assertEqual(request.url.params["up_mid"], "123")
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                return httpx.Response(200, json=FOLDER_PAYLOAD)

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_favorite_folders(
                    "123",
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual(
            response.model_dump(mode="json"),
            {
                "folders": [
                    {
                        "id": "1001",
                        "title": "默认收藏夹",
                        "cover_url": "https://i0.hdslb.com/folder.jpg",
                        "media_count": 2,
                    }
                ]
            },
        )

    def test_fetches_videos_with_folder_relationship(self) -> None:
        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    request.url.path,
                    "/x/v3/fav/resource/list",
                )
                self.assertEqual(request.url.params["media_id"], "1001")
                self.assertEqual(request.url.params["pn"], "2")
                self.assertEqual(request.url.params["ps"], "10")
                return httpx.Response(200, json=VIDEOS_PAYLOAD)

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_favorite_folder_videos(
                    "1001",
                    FavoriteVideosQuery(page=2, page_size=10),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        result = response.model_dump(mode="json")
        self.assertEqual(result["folder"]["id"], "1001")
        self.assertEqual(result["videos"][0]["folder_id"], "1001")
        self.assertEqual(
            result["videos"][0]["favorited_at"],
            "2025-09-10T02:46:40Z",
        )
        self.assertEqual(result["page"], 2)
        self.assertTrue(result["has_more"])

    def test_empty_folder_accepts_null_medias(self) -> None:
        payload = {
            "code": 0,
            "message": "0",
            "data": {
                "info": {
                    "id": 1001,
                    "title": "空收藏夹",
                    "cover": "",
                    "media_count": 0,
                },
                "medias": None,
                "has_more": False,
            },
        }

        async def run_scenario():
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )

            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch_favorite_folder_videos(
                    "1001",
                    FavoriteVideosQuery(),
                    "SESSDATA=test",
                    client,
                )

        response = asyncio.run(run_scenario())
        self.assertEqual(response.videos, [])
        self.assertFalse(response.has_more)


if __name__ == "__main__":
    unittest.main()
