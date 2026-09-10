from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bili_agent_cli.main import app
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.following import FollowingFeedResponse
from bili_agent_cli.schemas.watch_later import (
    WatchLaterResponse,
    WatchLaterVideo,
    WatchLaterVideoAuthor,
)
from bili_agent_cli.schemas.search import SearchVideoResponse
from tests.test_following import EXPECTED_FOLLOWING_RESPONSE
from tests.test_search_schemas import EXPECTED_SEARCH_RESPONSE


class MainTest(unittest.TestCase):
    async def _request(
        self,
        path: str,
        parameters: dict[str, str] | None = None,
    ) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path, params=parameters)

    def test_health(self) -> None:
        response = asyncio.run(self._request("/health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.text, '{\n    "status": "ok"\n}')

    def test_following_route_returns_response_model(self) -> None:
        parsed_response = FollowingFeedResponse.model_validate(
            EXPECTED_FOLLOWING_RESPONSE
        )

        with (
            patch(
                "bili_agent_cli.routes.following.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.following.fetch_following_feed",
                new=AsyncMock(return_value=parsed_response),
            ) as fetch_mock,
        ):
            response = asyncio.run(
                self._request(
                    "/api/following/feed",
                    {"offset": "current-offset"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), EXPECTED_FOLLOWING_RESPONSE)
        query = fetch_mock.await_args.args[0]
        self.assertEqual(query.offset, "current-offset")

    def test_favorite_folders_route_returns_response_model(self) -> None:
        parsed_response = FavoriteFolderListResponse(
            folders=[
                FavoriteFolder(
                    id="1001",
                    title="默认收藏夹",
                    cover_url=None,
                    media_count=1,
                )
            ]
        )

        with (
            patch(
                "bili_agent_cli.routes.favorites.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.favorites.fetch_favorite_folders",
                new=AsyncMock(return_value=parsed_response),
            ) as fetch_mock,
        ):
            response = asyncio.run(self._request("/api/favorites/folders"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), parsed_response.model_dump(mode="json"))
        self.assertEqual(fetch_mock.await_args.args[0], "123")
        self.assertEqual(fetch_mock.await_args.args[1], "SESSDATA=test-value")

    def test_favorite_videos_route_keeps_folder_relationship(self) -> None:
        folder = FavoriteFolder(
            id="1001",
            title="默认收藏夹",
            cover_url=None,
            media_count=1,
        )
        parsed_response = FavoriteFolderVideosResponse(
            folder=folder,
            videos=[
                FavoriteVideo(
                    folder_id=folder.id,
                    bvid="BV1favorite",
                    title="收藏的视频",
                    cover_url="https://i0.hdslb.com/favorite.jpg",
                    duration_seconds=120,
                    favorited_at=1_757_472_400,
                    author=FavoriteVideoAuthor(
                        mid="456",
                        name="测试UP主",
                        avatar_url="https://i0.hdslb.com/avatar.jpg",
                    ),
                )
            ],
            page=2,
            page_size=10,
            has_more=False,
        )

        with (
            patch(
                "bili_agent_cli.routes.favorites.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.favorites.fetch_favorite_folder_videos",
                new=AsyncMock(return_value=parsed_response),
            ) as fetch_mock,
        ):
            response = asyncio.run(
                self._request(
                    "/api/favorites/folders/1001/videos",
                    {"page": "2", "page_size": "10"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), parsed_response.model_dump(mode="json"))
        self.assertEqual(fetch_mock.await_args.args[0], "1001")
        query = fetch_mock.await_args.args[1]
        self.assertEqual(query.page, 2)
        self.assertEqual(query.page_size, 10)

    def test_watch_later_route_returns_response_model(self) -> None:
        parsed_response = WatchLaterResponse(
            videos=[
                WatchLaterVideo(
                    bvid="BV1later",
                    cid="789",
                    title="稍后再看的视频",
                    cover_url="https://i0.hdslb.com/later.jpg",
                    duration_seconds=300,
                    progress_seconds=60,
                    published_at=1_757_472_400,
                    author=WatchLaterVideoAuthor(
                        mid="456",
                        name="测试UP主",
                        avatar_url="https://i0.hdslb.com/avatar.jpg",
                    ),
                )
            ],
            total_count=1,
            page=1,
            page_size=20,
            has_more=False,
        )

        with (
            patch(
                "bili_agent_cli.routes.watch_later.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.watch_later.fetch_watch_later",
                new=AsyncMock(return_value=parsed_response),
            ) as fetch_mock,
        ):
            response = asyncio.run(
                self._request(
                    "/api/watch-later",
                    {"page": "1", "ascending": "true"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), parsed_response.model_dump(mode="json"))
        query = fetch_mock.await_args.args[0]
        self.assertTrue(query.ascending)

    def test_search_videos_route_returns_response_model(self) -> None:
        parsed_response = SearchVideoResponse.model_validate(
            EXPECTED_SEARCH_RESPONSE
        )

        with (
            patch(
                "bili_agent_cli.routes.search.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.search.search_videos",
                new=AsyncMock(return_value=parsed_response),
            ) as search_mock,
        ):
            response = asyncio.run(
                self._request(
                    "/api/search/videos",
                    {
                        "keyword": "Python 教程",
                        "order": "pubdate",
                        "duration": "2",
                    },
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), EXPECTED_SEARCH_RESPONSE)
        query = search_mock.await_args.args[0]
        self.assertEqual(query.keyword, "Python 教程")
        self.assertEqual(query.order.value, "pubdate")
        self.assertEqual(query.duration.value, 2)


if __name__ == "__main__":
    unittest.main()
