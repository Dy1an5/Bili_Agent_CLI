from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, call, patch
from urllib.parse import parse_qs

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.favorites import (
    FavoriteWriteError,
    execute_favorite_save,
    fetch_favorite_folder_videos,
    fetch_favorite_folders,
    prepare_favorite_save,
    save_videos_to_favorite_folder,
)
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderPrivacy,
    FavoriteSavePlan,
    FavoriteSaveStatus,
    FavoriteSaveVideo,
    FavoriteVideosQuery,
    SaveVideosToFavoriteFolderRequest,
)


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
                "intro": "收藏视频的简介",
                "cover": "http://i0.hdslb.com/favorite.jpg",
                "duration": 125,
                "fav_time": 1_757_472_400,
                "cnt_info": {
                    "play": 12_345,
                    "danmaku": 67,
                },
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
        self.assertEqual(result["videos"][0]["description"], "收藏视频的简介")
        self.assertEqual(result["videos"][0]["stats"]["views"], 12_345)
        self.assertEqual(result["videos"][0]["stats"]["danmaku"], 67)
        self.assertIsNone(result["videos"][0]["stats"]["likes"])
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

    def test_save_uses_existing_folder_and_single_video_form(self) -> None:
        requests: list[httpx.Request] = []

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                if request.url.path == "/x/web-interface/view":
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "aid": 42,
                                "bvid": request.url.params["bvid"],
                                "title": "测试视频",
                            },
                        },
                    )
                if request.url.path == "/x/v3/fav/folder/created/list-all":
                    return httpx.Response(200, json=FOLDER_PAYLOAD)
                if request.url.path == "/x/v3/fav/resource/deal":
                    return httpx.Response(200, json={"code": 0, "data": None})
                self.fail(f"unexpected path: {request.url.path}")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await save_videos_to_favorite_folder(
                    SaveVideosToFavoriteFolderRequest(
                        folder_title="默认收藏夹",
                        bvids=["BV1favorite"],
                    ),
                    "123",
                    "SESSDATA=session-value",
                    "SESSDATA=session-value; bili_jct=csrf-value",
                    "csrf-value",
                    client,
                )

        result = asyncio.run(run_scenario())
        self.assertEqual(result.status, FavoriteSaveStatus.COMPLETED)
        self.assertFalse(result.folder.created)
        self.assertEqual(result.added_count, 1)
        self.assertNotIn(
            "/x/v3/fav/folder/add",
            [request.url.path for request in requests],
        )
        write_request = requests[-1]
        self.assertEqual(write_request.method, "POST")
        self.assertEqual(
            write_request.headers["cookie"],
            "SESSDATA=session-value; bili_jct=csrf-value",
        )
        form = parse_qs(
            write_request.content.decode(),
            keep_blank_values=True,
        )
        self.assertNotIn("resources", form)
        self.assertEqual(form["rid"], ["42"])
        self.assertEqual(form["type"], ["2"])
        self.assertEqual(form["add_media_ids"], ["1001"])
        self.assertEqual(form["del_media_ids"], [""])
        self.assertEqual(form["csrf"], ["csrf-value"])

    def test_save_creates_private_folder_before_adding_videos(self) -> None:
        posts: list[tuple[str, dict[str, list[str]]]] = []

        async def run_scenario():
            folder_list_count = 0

            def handle_request(request: httpx.Request) -> httpx.Response:
                nonlocal folder_list_count
                if request.url.path == "/x/web-interface/view":
                    return httpx.Response(
                        200,
                        json={"code": 0, "data": {"aid": 99, "title": "视频"}},
                    )
                if request.url.path == "/x/v3/fav/folder/created/list-all":
                    folder_list_count += 1
                    return httpx.Response(
                        200,
                        json={"code": 0, "data": {"list": []}},
                    )
                form = parse_qs(
                    request.content.decode(),
                    keep_blank_values=True,
                )
                posts.append((request.url.path, form))
                if request.url.path == "/x/v3/fav/folder/add":
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "id": 2002,
                                "title": "自动收藏",
                                "cover": "",
                                "media_count": 0,
                            },
                        },
                    )
                return httpx.Response(200, json={"code": 0, "data": None})

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await save_videos_to_favorite_folder(
                    SaveVideosToFavoriteFolderRequest(
                        folder_title="自动收藏",
                        bvids=["BV1favorite"],
                    ),
                    "123",
                    "SESSDATA=session-value",
                    "SESSDATA=session-value; bili_jct=csrf-value",
                    "csrf-value",
                    client,
                )

        result = asyncio.run(run_scenario())
        self.assertEqual(result.status, FavoriteSaveStatus.COMPLETED)
        self.assertTrue(result.folder.created)
        self.assertEqual([path for path, _ in posts], [
            "/x/v3/fav/folder/add",
            "/x/v3/fav/resource/deal",
        ])
        self.assertEqual(posts[0][1]["privacy"], ["1"])
        self.assertEqual(posts[0][1]["csrf"], ["csrf-value"])

    def test_prepare_failure_performs_no_write(self) -> None:
        methods: list[str] = []

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                methods.append(request.method)
                return httpx.Response(
                    200,
                    json={"code": -400, "message": "请求错误"},
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                with self.assertRaises(FavoriteWriteError):
                    await prepare_favorite_save(
                        SaveVideosToFavoriteFolderRequest(
                            folder_title="目标收藏夹",
                            bvids=["BV1favorite"],
                        ),
                        "123",
                        "SESSDATA=test",
                        client,
                    )

        asyncio.run(run_scenario())
        self.assertEqual(methods, ["GET"])

    def test_created_folder_is_retained_when_item_business_error_occurs(self) -> None:
        plan = FavoriteSavePlan(
            folder_title="自动收藏",
            will_create_folder=True,
            privacy=FavoriteFolderPrivacy.PUBLIC,
            videos=[FavoriteSaveVideo(bvid="BV1favorite", aid="99")],
        )
        privacy_values: list[str] = []

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.method == "GET":
                    return httpx.Response(
                        200,
                        json={"code": 0, "data": {"list": []}},
                    )
                form = parse_qs(request.content.decode())
                if request.url.path == "/x/v3/fav/folder/add":
                    privacy_values.extend(form["privacy"])
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "id": 2002,
                                "title": "自动收藏",
                                "cover": "",
                                "media_count": 0,
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={"code": -400, "message": "添加失败"},
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await execute_favorite_save(
                    plan,
                    "123",
                    "SESSDATA=test",
                    "SESSDATA=test; bili_jct=csrf",
                    "csrf",
                    client,
                )

        result = asyncio.run(run_scenario())
        self.assertEqual(privacy_values, ["0"])
        self.assertEqual(result.status, FavoriteSaveStatus.PARTIAL)
        self.assertTrue(result.folder.created)
        self.assertEqual(result.added_count, 0)
        self.assertEqual(
            [video.bvid for video in result.retry_videos],
            ["BV1favorite"],
        )
        self.assertEqual(result.upstream_code, -400)
        self.assertIn("添加失败", result.upstream_message)
        self.assertTrue(result.retryable)

    def test_item_business_error_tracks_failed_videos(self) -> None:
        plan = FavoriteSavePlan(
            folder_title="默认收藏夹",
            existing_folder_id="1001",
            will_create_folder=False,
            privacy=FavoriteFolderPrivacy.PRIVATE,
            videos=[
                FavoriteSaveVideo(bvid="BV1success", aid="41"),
                FavoriteSaveVideo(bvid="BV1failure", aid="42"),
            ],
        )
        write_forms: list[tuple[str, dict[str, list[str]]]] = []

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.method == "GET":
                    return httpx.Response(200, json=FOLDER_PAYLOAD)
                form = parse_qs(
                    request.content.decode(),
                    keep_blank_values=True,
                )
                write_forms.append((request.url.path, form))
                if form["rid"] == ["41"]:
                    return httpx.Response(200, json={"code": 0, "data": {}})
                return httpx.Response(
                    200,
                    json={"code": 11203, "message": "达到收藏上限"},
                )

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await execute_favorite_save(
                    plan,
                    "123",
                    "SESSDATA=test",
                    "SESSDATA=test; bili_jct=csrf",
                    "csrf",
                    client,
                )

        result = asyncio.run(run_scenario())
        self.assertEqual(
            [path for path, _ in write_forms],
            [
                "/x/v3/fav/resource/deal",
                "/x/v3/fav/resource/deal",
            ],
        )
        self.assertEqual(write_forms[0][1]["rid"], ["41"])
        self.assertEqual(write_forms[1][1]["rid"], ["42"])
        self.assertEqual(result.status, FavoriteSaveStatus.PARTIAL)
        self.assertEqual(result.added_count, 1)
        self.assertEqual(
            [video.bvid for video in result.retry_videos],
            ["BV1failure"],
        )
        self.assertEqual(result.upstream_code, 11203)

    def test_item_transport_error_has_unknown_outcome(self) -> None:
        plan = FavoriteSavePlan(
            folder_title="默认收藏夹",
            existing_folder_id="1001",
            will_create_folder=False,
            privacy=FavoriteFolderPrivacy.PRIVATE,
            videos=[FavoriteSaveVideo(bvid="BV1favorite", aid="99")],
        )

        async def run_scenario():
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.method == "GET":
                    return httpx.Response(200, json=FOLDER_PAYLOAD)
                raise httpx.ConnectError("连接中断", request=request)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await execute_favorite_save(
                    plan,
                    "123",
                    "SESSDATA=test",
                    "SESSDATA=test; bili_jct=csrf",
                    "csrf",
                    client,
                )

        result = asyncio.run(run_scenario())
        self.assertEqual(result.status, FavoriteSaveStatus.OUTCOME_UNKNOWN)
        self.assertIsNone(result.added_count)
        self.assertEqual(result.retry_videos, plan.videos)
        self.assertIn("网络请求失败", result.upstream_message)
        self.assertTrue(result.retryable)

    def test_write_pauses_after_every_five_attempted_videos(self) -> None:
        plan = FavoriteSavePlan(
            folder_title="默认收藏夹",
            existing_folder_id="1001",
            will_create_folder=False,
            privacy=FavoriteFolderPrivacy.PRIVATE,
            videos=[
                FavoriteSaveVideo(bvid=f"BV1write{index:02d}", aid=str(index))
                for index in range(1, 8)
            ],
        )
        write_count = 0

        async def run_scenario():
            nonlocal write_count

            def handle_request(request: httpx.Request) -> httpx.Response:
                nonlocal write_count
                if request.method == "GET":
                    return httpx.Response(200, json=FOLDER_PAYLOAD)
                self.assertEqual(
                    request.url.path,
                    "/x/v3/fav/resource/deal",
                )
                write_count += 1
                return httpx.Response(200, json={"code": 0, "data": {}})

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await execute_favorite_save(
                    plan,
                    "123",
                    "SESSDATA=test",
                    "SESSDATA=test; bili_jct=csrf",
                    "csrf",
                    client,
                )

        with patch(
            "bili_agent_cli.bilibili.favorites.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            result = asyncio.run(run_scenario())

        self.assertEqual(result.status, FavoriteSaveStatus.COMPLETED)
        self.assertEqual(result.added_count, 7)
        self.assertEqual(write_count, 7)
        sleep_mock.assert_awaited_once_with(2.0)

    def test_rate_limit_uses_exponential_backoff_then_succeeds(self) -> None:
        plan = FavoriteSavePlan(
            folder_title="默认收藏夹",
            existing_folder_id="1001",
            will_create_folder=False,
            privacy=FavoriteFolderPrivacy.PRIVATE,
            videos=[FavoriteSaveVideo(bvid="BV1favorite", aid="99")],
        )
        write_count = 0

        async def run_scenario():
            nonlocal write_count

            def handle_request(request: httpx.Request) -> httpx.Response:
                nonlocal write_count
                if request.method == "GET":
                    return httpx.Response(200, json=FOLDER_PAYLOAD)
                write_count += 1
                if write_count < 3:
                    return httpx.Response(
                        200,
                        json={"code": -702, "message": "请求频率过高"},
                    )
                return httpx.Response(200, json={"code": 0, "data": {}})

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await execute_favorite_save(
                    plan,
                    "123",
                    "SESSDATA=test",
                    "SESSDATA=test; bili_jct=csrf",
                    "csrf",
                    client,
                )

        with patch(
            "bili_agent_cli.bilibili.favorites.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            result = asyncio.run(run_scenario())

        self.assertEqual(result.status, FavoriteSaveStatus.COMPLETED)
        self.assertEqual(result.added_count, 1)
        self.assertEqual(write_count, 3)
        self.assertEqual(sleep_mock.await_args_list, [call(3.0), call(6.0)])


if __name__ == "__main__":
    unittest.main()
