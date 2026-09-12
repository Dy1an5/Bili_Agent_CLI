from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from bili_agent_cli.bilibili.history import HistoryError
from bili_agent_cli.main import app
from bili_agent_cli.agent.context import (
    ContextBudgetExceededError,
    SessionNotFoundError,
)
from bili_agent_cli.agent.models import AgentRunResponse, AgentSource
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.history import (
    HistoryResponse,
    HistoryVideo,
    HistoryVideoAuthor,
)
from bili_agent_cli.schemas.following import FollowingFeedResponse
from bili_agent_cli.schemas.watch_later import (
    WatchLaterResponse,
    WatchLaterVideo,
    WatchLaterVideoAuthor,
)
from bili_agent_cli.profile import ProfileError
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

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
    ) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, json=payload)

    async def _delete(self, path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.delete(path)

    def test_health(self) -> None:
        response = asyncio.run(self._request("/health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.text, '{\n    "status": "ok"\n}')

    def test_agent_route_keeps_public_contract(self) -> None:
        session_id = uuid4()
        result = AgentRunResponse(
            answer="测试回答",
            session_id=session_id,
            sources=[
                AgentSource(
                    bvid="BV1source",
                    title="引用视频",
                    source_tools=["search_videos"],
                )
            ],
        )

        with patch(
            "bili_agent_cli.routes.agent.run_agent",
            new=AsyncMock(return_value=result),
        ) as run_mock:
            response = asyncio.run(
                self._post(
                    "/agent/run",
                    {"task": "测试问题", "session_id": None},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "测试回答")
        self.assertEqual(response.json()["session_id"], str(session_id))
        self.assertEqual(
            response.json()["sources"][0]["source_id"],
            "bilibili:video:BV1source",
        )
        self.assertEqual(
            response.json()["sources"][0]["source_tools"],
            ["search_videos"],
        )
        run_mock.assert_awaited_once_with("测试问题", None)

    def test_missing_agent_session_returns_404(self) -> None:
        with patch(
            "bili_agent_cli.routes.agent.run_agent",
            new=AsyncMock(side_effect=SessionNotFoundError),
        ):
            response = asyncio.run(
                self._post(
                    "/agent/run",
                    {"task": "继续会话", "session_id": str(uuid4())},
                )
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": {"code": "SESSION_NOT_FOUND"}},
        )

    def test_oversized_agent_context_returns_413(self) -> None:
        with patch(
            "bili_agent_cli.routes.agent.run_agent",
            new=AsyncMock(side_effect=ContextBudgetExceededError),
        ):
            response = asyncio.run(
                self._post(
                    "/agent/run",
                    {"task": "超大上下文", "session_id": None},
                )
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json(),
            {"detail": {"code": "CONTEXT_BUDGET_EXCEEDED"}},
        )

    def test_delete_agent_session_returns_204(self) -> None:
        session_id = uuid4()
        with patch(
            "bili_agent_cli.routes.agent.session_store.delete",
            new=AsyncMock(return_value=None),
        ) as delete_mock:
            response = asyncio.run(
                self._delete(f"/agent/sessions/{session_id}")
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        delete_mock.assert_awaited_once_with(session_id)

    def test_delete_missing_agent_session_returns_404(self) -> None:
        with patch(
            "bili_agent_cli.routes.agent.session_store.delete",
            new=AsyncMock(side_effect=SessionNotFoundError),
        ):
            response = asyncio.run(
                self._delete(f"/agent/sessions/{uuid4()}")
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": {"code": "SESSION_NOT_FOUND"}},
        )

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

    def test_history_route_returns_response_model(self) -> None:
        parsed_response = HistoryResponse(
            videos=[
                HistoryVideo(
                    bvid="BV1history",
                    cid="789",
                    title="看过的视频",
                    cover_url="https://i0.hdslb.com/history.jpg",
                    viewed_at=1_757_472_400,
                    progress_seconds=60,
                    duration_seconds=300,
                    is_favorite=False,
                    author=HistoryVideoAuthor(
                        mid="456",
                        name="历史UP主",
                        avatar_url=None,
                    ),
                )
            ],
            page_size=10,
            has_more=True,
            next_max=123,
            next_view_at=1_757_472_400,
        )

        with (
            patch(
                "bili_agent_cli.routes.history.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.history.fetch_watch_history",
                new=AsyncMock(return_value=parsed_response),
            ) as fetch_mock,
        ):
            response = asyncio.run(
                self._request(
                    "/api/history",
                    {"page_size": "10", "max": "999", "view_at": "888"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), parsed_response.model_dump(mode="json"))
        query = fetch_mock.await_args.args[0]
        self.assertEqual(query.page_size, 10)
        self.assertEqual(query.max, 999)
        self.assertEqual(query.view_at, 888)

    def test_history_route_maps_profile_and_upstream_errors(self) -> None:
        with patch(
            "bili_agent_cli.routes.history.load_profile",
            side_effect=ProfileError("未登录"),
        ):
            unauthorized = asyncio.run(self._request("/api/history"))

        with (
            patch(
                "bili_agent_cli.routes.history.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.routes.history.fetch_watch_history",
                new=AsyncMock(side_effect=HistoryError("上游错误")),
            ),
        ):
            bad_gateway = asyncio.run(self._request("/api/history"))

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json(), {"detail": "未登录"})
        self.assertEqual(bad_gateway.status_code, 502)
        self.assertEqual(bad_gateway.json(), {"detail": "上游错误"})

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
