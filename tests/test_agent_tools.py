from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from bili_agent_cli.agent.executor import execute_tool
from bili_agent_cli.agent.models import AgentSource
from bili_agent_cli.agent.registry import TOOL_REGISTRY, build_tool_schemas
from bili_agent_cli.agent.result_models import (
    LlmFollowingUsersResult,
    LlmUserDynamicsResult,
    LlmVideoSubtitleResult,
)
from bili_agent_cli.bilibili.following_users import FollowingUsersError
from bili_agent_cli.bilibili.subtitles import VideoSubtitleError
from bili_agent_cli.content.subtitles import VideoSubtitleStorageError
from bili_agent_cli.bilibili.user_dynamics import UserDynamicsError
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.common import VideoStats
from bili_agent_cli.schemas.history import (
    HistoryResponse,
    HistoryVideo,
    HistoryVideoAuthor,
)
from bili_agent_cli.schemas.following_users import (
    FollowingOfficialVerification,
    FollowingUser,
    FollowingUsersResponse,
)
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicAuthor,
    UserDynamicContent,
    UserDynamicItem,
    UserDynamicsResponse,
    UserDynamicStats,
)
from bili_agent_cli.schemas.subtitles import (
    SubtitleCue,
    SubtitleStatus,
    SubtitleTrack,
    SubtitleTrackSource,
    VideoSubtitleResponse,
)


class AgentToolsTest(unittest.TestCase):
    def test_content_tools_are_registered(self) -> None:
        self.assertEqual(
            set(TOOL_REGISTRY),
            {
                "get_following_feed",
                "get_following_users",
                "get_favorite_folders",
                "get_favorite_folder_videos",
                "get_watch_later",
                "get_watch_history",
                "search_videos",
                "get_video_subtitle",
                "get_user_dynamics",
                "get_user_profile",
                "prepare_save_videos_to_favorite_folder",
                "commit_save_videos_to_favorite_folder",
            },
        )

        schemas = {
            item["function"]["name"]: item["function"]
            for item in build_tool_schemas()
        }
        folder_parameters = schemas["get_favorite_folders"]["parameters"]
        video_parameters = schemas["get_favorite_folder_videos"]["parameters"]
        self.assertEqual(folder_parameters["properties"], {})
        self.assertIn("folder_id", video_parameters["required"])
        self.assertIn("keyword", schemas["search_videos"]["parameters"]["required"])
        subtitle_parameters = schemas["get_video_subtitle"]["parameters"]
        self.assertFalse(subtitle_parameters["additionalProperties"])
        self.assertEqual(subtitle_parameters["required"], ["bvid", "cid"])
        self.assertEqual(subtitle_parameters["properties"]["offset"]["default"], 0)
        self.assertEqual(subtitle_parameters["properties"]["limit"]["maximum"], 200)
        self.assertIn(
            "next_offset",
            schemas["get_video_subtitle"]["description"],
        )
        history_parameters = schemas["get_watch_history"]["parameters"]
        self.assertIn("max", history_parameters["properties"])
        self.assertIn("view_at", history_parameters["properties"])
        following_parameters = schemas["get_following_users"]["parameters"]
        self.assertFalse(following_parameters["additionalProperties"])
        self.assertEqual(following_parameters["properties"]["page"]["default"], 1)
        self.assertEqual(
            following_parameters["properties"]["page_size"]["maximum"],
            50,
        )
        self.assertEqual(
            following_parameters["$defs"]["FollowingUsersSort"]["enum"],
            ["recent", "frequent"],
        )
        self.assertIn(
            "最近关注",
            following_parameters["properties"]["sort"]["description"],
        )
        dynamics_parameters = schemas["get_user_dynamics"]["parameters"]
        self.assertFalse(dynamics_parameters["additionalProperties"])
        self.assertEqual(dynamics_parameters["required"], ["user_mid"])
        self.assertEqual(
            dynamics_parameters["properties"]["user_mid"]["pattern"],
            r"^[1-9]\d*$",
        )
        self.assertIn(
            "get_following_users",
            dynamics_parameters["properties"]["user_mid"]["description"],
        )
        self.assertIn(
            "next_offset",
            schemas["get_user_dynamics"]["description"],
        )
        self.assertEqual(
            dynamics_parameters["properties"]["offset"]["default"],
            "",
        )
        prepare_parameters = schemas[
            "prepare_save_videos_to_favorite_folder"
        ]["parameters"]
        self.assertIn("source_ids", prepare_parameters["required"])
        self.assertEqual(
            prepare_parameters["properties"]["privacy"]["default"],
            "private",
        )
        self.assertIn(
            "新的用户轮次",
            schemas["prepare_save_videos_to_favorite_folder"]["description"],
        )
        commit_parameters = schemas[
            "commit_save_videos_to_favorite_folder"
        ]["parameters"]
        self.assertEqual(commit_parameters["required"], ["confirmation_id"])
        self.assertIn("submit_agent_answer", schemas)
        self.assertIn(
            "source_ids",
            schemas["submit_agent_answer"]["parameters"]["properties"],
        )
        persona_parameters = schemas["get_user_profile"]["parameters"]
        self.assertTrue(
            persona_parameters["properties"]["refresh"]["default"]
        )
        self.assertEqual(
            persona_parameters["properties"]["max_new_videos"]["default"],
            60,
        )

    def test_agent_source_keeps_old_fields_and_derives_source_id(self) -> None:
        source = AgentSource(bvid="BV1compatible", title="兼容来源")

        self.assertEqual(source.source_id, "bilibili:video:BV1compatible")
        self.assertEqual(source.source_tools, [])

    def test_agent_source_uses_cid_but_accepts_legacy_saved_id(self) -> None:
        current = AgentSource(bvid="BV1compatible", cid="123")
        legacy = AgentSource(
            bvid="BV1compatible",
            cid="123",
            source_id="bilibili:video:BV1compatible",
        )

        self.assertEqual(
            current.source_id,
            "bilibili:video:BV1compatible:part:123",
        )
        self.assertEqual(legacy.source_id, "bilibili:video:BV1compatible")

    def test_executes_favorite_folder_tool(self) -> None:
        response = FavoriteFolderListResponse(
            folders=[
                FavoriteFolder(
                    id="1001",
                    title="Concert",
                    cover_url=None,
                    media_count=6,
                )
            ]
        )

        with patch(
            "bili_agent_cli.agent.registry.get_favorite_folders_tool",
            new=AsyncMock(return_value=response),
        ) as tool_mock:
            result = asyncio.run(execute_tool("get_favorite_folders", {}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["folders"][0]["title"], "Concert")
        self.assertEqual(tool_mock.await_args.args[0].model_dump(), {})

    def test_rejects_non_numeric_favorite_folder_id(self) -> None:
        result = asyncio.run(
            execute_tool(
                "get_favorite_folder_videos",
                {"folder_id": "Concert", "page": 1, "page_size": 20},
            )
        )

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_TOOL_ARGUMENTS"},
        )

    def test_projects_full_result_to_llm_result(self) -> None:
        folder = FavoriteFolder(
            id="1001",
            title="Concert",
            cover_url="https://i0.hdslb.com/folder.jpg",
            media_count=1,
        )
        response = FavoriteFolderVideosResponse(
            folder=folder,
            videos=[
                FavoriteVideo(
                    folder_id=folder.id,
                    bvid="BV1favorite",
                    title="收藏的视频",
                    description="视频简介",
                    cover_url="https://i0.hdslb.com/video.jpg",
                    duration_seconds=120,
                    favorited_at=1_757_472_400,
                    author=FavoriteVideoAuthor(
                        mid="456",
                        name="测试UP主",
                        avatar_url="https://i0.hdslb.com/avatar.jpg",
                    ),
                    stats=VideoStats(views=1000, danmaku=20),
                )
            ],
            page=1,
            page_size=20,
            has_more=False,
        )

        with patch(
            "bili_agent_cli.agent.registry.get_favorite_folder_videos_tool",
            new=AsyncMock(return_value=response),
        ):
            result = asyncio.run(
                execute_tool(
                    "get_favorite_folder_videos",
                    {"folder_id": "1001"},
                )
            )

        self.assertTrue(result["ok"])
        self.assertNotIn("cover_url", result["data"]["folder"])
        video = result["data"]["videos"][0]
        self.assertNotIn("cover_url", video)
        self.assertNotIn("avatar_url", video["author"])
        self.assertEqual(
            video["source_id"],
            "bilibili:video:BV1favorite",
        )
        self.assertEqual(video["description"], "视频简介")
        self.assertEqual(video["stats"]["views"], 1000)
        self.assertIsNone(video["stats"]["likes"])

    def test_projects_history_result_and_adds_source_id(self) -> None:
        response = HistoryResponse(
            videos=[
                HistoryVideo(
                    bvid="BV1history",
                    cid="789",
                    title="看过的视频",
                    cover_url="https://i0.hdslb.com/history.jpg",
                    viewed_at=1_757_472_400,
                    progress_seconds=-1,
                    duration_seconds=300,
                    is_favorite=True,
                    author=HistoryVideoAuthor(
                        mid="456",
                        name="历史UP主",
                        avatar_url="https://i0.hdslb.com/avatar.jpg",
                    ),
                )
            ],
            page_size=20,
            has_more=True,
            next_max=123,
            next_view_at=1_757_472_400,
        )

        with patch(
            "bili_agent_cli.agent.registry.get_watch_history_tool",
            new=AsyncMock(return_value=response),
        ):
            result = asyncio.run(execute_tool("get_watch_history", {}))

        self.assertTrue(result["ok"])
        video = result["data"]["videos"][0]
        self.assertEqual(video["source_id"], "bilibili:video:BV1history")
        self.assertEqual(video["viewed_at"], "2025-09-10 10:46 (UTC+8)")
        self.assertNotIn("cover_url", video)
        self.assertNotIn("avatar_url", video["author"])

    @staticmethod
    def _following_users_response() -> FollowingUsersResponse:
        return FollowingUsersResponse(
            users=[
                FollowingUser(
                    mid="456",
                    name="测试UP主",
                    avatar_url="https://i0.hdslb.com/avatar.jpg",
                    signature="测试签名",
                    followed_at=1_757_472_400,
                    is_mutual=True,
                    is_special=False,
                    official_verification=FollowingOfficialVerification(
                        type=0,
                        description="官方账号",
                    ),
                )
            ],
            total=21,
            page=1,
            page_size=20,
            has_more=True,
        )

    def test_executes_following_users_tool_for_current_account(self) -> None:
        response = self._following_users_response()

        with (
            patch(
                "bili_agent_cli.agent.tools.bili.get_following_users.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "csrf-value",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.get_following_users.fetch_following_users",
                new=AsyncMock(return_value=response),
            ) as fetch_mock,
        ):
            result = asyncio.run(
                execute_tool(
                    "get_following_users",
                    {"page": 1, "page_size": 20, "sort": "recent"},
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(fetch_mock.await_args.args[0], "123")
        self.assertEqual(fetch_mock.await_args.args[1].sort.value, "recent")
        self.assertEqual(fetch_mock.await_args.args[2], "SESSDATA=test-value")
        user = result["data"]["users"][0]
        self.assertEqual(user["mid"], "456")
        self.assertEqual(user["followed_at"], "2025-09-10 10:46 (UTC+8)")
        self.assertNotIn("avatar_url", user)
        self.assertEqual(result["data"]["page"], 1)
        self.assertTrue(result["data"]["has_more"])

    def test_rejects_invalid_following_users_arguments(self) -> None:
        result = asyncio.run(
            execute_tool(
                "get_following_users",
                {"page": 0, "unknown": True},
            )
        )

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_TOOL_ARGUMENTS"},
        )

    def test_maps_following_users_domain_error_to_stable_code(self) -> None:
        with patch(
            "bili_agent_cli.agent.registry.get_following_users_tool",
            new=AsyncMock(side_effect=FollowingUsersError("sensitive detail")),
        ):
            result = asyncio.run(execute_tool("get_following_users", {}))

        self.assertEqual(
            result,
            {"ok": False, "error": "FOLLOWING_USERS_FETCH_ERROR"},
        )
        self.assertNotIn("sensitive", str(result))

    def test_rejects_invalid_following_users_result(self) -> None:
        definition = TOOL_REGISTRY["get_following_users"]
        invalid_definition = replace(
            definition,
            executor=AsyncMock(return_value={"users": []}),
        )

        with patch.dict(
            TOOL_REGISTRY,
            {"get_following_users": invalid_definition},
        ):
            result = asyncio.run(execute_tool("get_following_users", {}))

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_TOOL_RESULT"},
        )

    def test_rejects_invalid_following_users_projection(self) -> None:
        definition = TOOL_REGISTRY["get_following_users"]

        def invalid_projector(result):
            return LlmFollowingUsersResult.model_validate({})

        invalid_definition = replace(
            definition,
            executor=AsyncMock(return_value=self._following_users_response()),
            result_projector=invalid_projector,
        )

        with patch.dict(
            TOOL_REGISTRY,
            {"get_following_users": invalid_definition},
        ):
            result = asyncio.run(execute_tool("get_following_users", {}))

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_LLM_TOOL_RESULT"},
        )

    @staticmethod
    def _video_subtitle_response() -> VideoSubtitleResponse:
        track = SubtitleTrack(
            language="zh-CN",
            display_name="中文",
            source=SubtitleTrackSource.HUMAN,
        )
        return VideoSubtitleResponse(
            status=SubtitleStatus.AVAILABLE,
            bvid="BV1subtitle",
            cid="987",
            track=track,
            available_tracks=[track],
            cues=[
                SubtitleCue(
                    index=0,
                    start_ms=1000,
                    end_ms=2500,
                    text="字幕内容",
                )
            ],
            source_hash="sha256:" + "a" * 64,
            total_cues=2,
            offset=0,
            limit=1,
            has_more=True,
            next_offset=1,
            fetched_at="2026-09-14T04:00:00Z",
            cached=True,
        )

    def test_executes_video_subtitle_tool_and_projects_compact_result(self) -> None:
        response = self._video_subtitle_response()
        with (
            patch(
                "bili_agent_cli.agent.tools.bili.get_video_subtitle.load_profile",
                return_value={
                    "SESSDATA": "test-value",
                    "bili_jct": "unused-csrf",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.get_video_subtitle.get_video_subtitle",
                new=AsyncMock(return_value=response),
            ) as service_mock,
        ):
            result = asyncio.run(
                execute_tool(
                    "get_video_subtitle",
                    {
                        "bvid": "BV1subtitle",
                        "cid": "987",
                        "language": "zh-CN",
                        "limit": 1,
                    },
                )
            )

        self.assertTrue(result["ok"])
        args = service_mock.await_args.args[0]
        self.assertEqual(args.bvid, "BV1subtitle")
        self.assertEqual(args.cid, "987")
        self.assertEqual(args.language, "zh-CN")
        self.assertEqual(service_mock.await_args.args[1], "SESSDATA=test-value")
        payload = result["data"]
        self.assertEqual(payload["source_id"], "bilibili:video:BV1subtitle")
        self.assertEqual(payload["cues"][0]["text"], "字幕内容")
        self.assertEqual(payload["next_offset"], 1)
        self.assertNotIn("cached", payload)
        self.assertNotIn("fetched_at", payload)

    def test_rejects_invalid_video_subtitle_arguments(self) -> None:
        for arguments in (
            {},
            {"bvid": "BV1subtitle", "cid": "0"},
            {"bvid": "BV1subtitle", "cid": "987", "limit": 201},
            {"bvid": "BV1subtitle", "cid": "987", "extra": True},
        ):
            with self.subTest(arguments=arguments):
                result = asyncio.run(
                    execute_tool("get_video_subtitle", arguments)
                )
                self.assertEqual(
                    result,
                    {"ok": False, "error": "INVALID_TOOL_ARGUMENTS"},
                )

    def test_maps_video_subtitle_domain_error_to_stable_code(self) -> None:
        with patch(
            "bili_agent_cli.agent.registry.get_video_subtitle_tool",
            new=AsyncMock(
                side_effect=VideoSubtitleError(
                    "sensitive upstream detail",
                    status=503,
                    code=-403,
                )
            ),
        ):
            result = asyncio.run(
                execute_tool(
                    "get_video_subtitle",
                    {"bvid": "BV1subtitle", "cid": "987"},
                )
            )

        self.assertEqual(
            result,
            {
                "ok": False,
                "error": "VIDEO_SUBTITLE_FETCH_ERROR",
                "details": {"upstream_code": -403, "http_status": 503},
            },
        )
        self.assertNotIn("sensitive", str(result))

        with patch(
            "bili_agent_cli.agent.registry.get_video_subtitle_tool",
            new=AsyncMock(
                side_effect=VideoSubtitleStorageError("private path")
            ),
        ):
            storage_result = asyncio.run(
                execute_tool(
                    "get_video_subtitle",
                    {"bvid": "BV1subtitle", "cid": "987"},
                )
            )
        self.assertEqual(
            storage_result,
            {"ok": False, "error": "VIDEO_SUBTITLE_STORAGE_ERROR"},
        )
        self.assertNotIn("private path", str(storage_result))

    def test_rejects_invalid_video_subtitle_result_and_projection(self) -> None:
        definition = TOOL_REGISTRY["get_video_subtitle"]
        invalid_result = replace(
            definition,
            executor=AsyncMock(return_value={"status": "available"}),
        )
        with patch.dict(
            TOOL_REGISTRY,
            {"get_video_subtitle": invalid_result},
        ):
            result = asyncio.run(
                execute_tool(
                    "get_video_subtitle",
                    {"bvid": "BV1subtitle", "cid": "987"},
                )
            )
        self.assertEqual(result, {"ok": False, "error": "INVALID_TOOL_RESULT"})

        def invalid_projector(result):
            return LlmVideoSubtitleResult.model_validate({})

        invalid_projection = replace(
            definition,
            executor=AsyncMock(return_value=self._video_subtitle_response()),
            result_projector=invalid_projector,
        )
        with patch.dict(
            TOOL_REGISTRY,
            {"get_video_subtitle": invalid_projection},
        ):
            result = asyncio.run(
                execute_tool(
                    "get_video_subtitle",
                    {"bvid": "BV1subtitle", "cid": "987"},
                )
            )
        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_LLM_TOOL_RESULT"},
        )

    @staticmethod
    def _user_dynamics_response() -> UserDynamicsResponse:
        return UserDynamicsResponse(
            user_mid="456",
            items=[
                UserDynamicItem(
                    dynamic_id="101",
                    type="DYNAMIC_TYPE_AV",
                    published_at=1_757_472_400,
                    visible=True,
                    is_pinned=False,
                    author=UserDynamicAuthor(
                        mid="456",
                        name="测试UP主",
                        avatar_url="https://i0.hdslb.com/avatar.jpg",
                    ),
                    content=UserDynamicContent(
                        major_type="MAJOR_TYPE_ARCHIVE",
                        title="动态视频",
                        text="动态正文",
                        bvid="BV1dynamic",
                        jump_url="https://www.bilibili.com/video/BV1dynamic",
                        cover_url="https://i0.hdslb.com/cover.jpg",
                        image_urls=["https://i0.hdslb.com/picture.jpg"],
                    ),
                    stats=UserDynamicStats(
                        likes=10,
                        replies=2,
                        reposts=1,
                        favorites=3,
                    ),
                )
            ],
            total_count=1,
            skipped_count=0,
            pages_fetched=1,
            has_more=True,
            next_offset="next-cursor",
        )

    def test_executes_user_dynamics_tool_and_projects_result(self) -> None:
        response = self._user_dynamics_response()
        with (
            patch(
                "bili_agent_cli.agent.tools.bili.get_user_dynamics.load_profile",
                return_value={"SESSDATA": "test-value"},
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.get_user_dynamics.fetch_user_dynamics_page",
                new=AsyncMock(return_value=response),
            ) as fetch_mock,
            patch(
                "bili_agent_cli.agent.tools.bili.get_user_dynamics.ingest_user_dynamics",
                new=AsyncMock(return_value=response),
            ) as ingest_mock,
        ):
            result = asyncio.run(
                execute_tool(
                    "get_user_dynamics",
                    {"user_mid": "456", "offset": "current-cursor"},
                )
            )

        self.assertTrue(result["ok"])
        fetch_mock.assert_awaited_once_with(
            "456",
            "current-cursor",
            "SESSDATA=test-value",
        )
        ingest_mock.assert_awaited_once_with(
            response,
            sessdata_cookie="SESSDATA=test-value",
        )
        self.assertEqual(result["data"]["user_mid"], "456")
        self.assertEqual(result["data"]["pages_fetched"], 1)
        self.assertTrue(result["data"]["has_more"])
        self.assertEqual(result["data"]["next_offset"], "next-cursor")
        item = result["data"]["items"][0]
        self.assertEqual(item["published_at"], "2025-09-10 10:46 (UTC+8)")
        self.assertEqual(
            item["content"]["source_id"],
            "bilibili:video:BV1dynamic",
        )
        self.assertNotIn("visible", item)
        self.assertNotIn("avatar_url", item["author"])
        self.assertNotIn("cover_url", item["content"])
        self.assertNotIn("image_urls", item["content"])

    def test_rejects_invalid_user_dynamics_arguments(self) -> None:
        for arguments in (
            {},
            {"user_mid": "0"},
            {"user_mid": "UP主名字"},
            {"user_mid": "456", "offset": "x" * 1001},
            {"user_mid": "456", "unknown": True},
        ):
            with self.subTest(arguments=arguments):
                result = asyncio.run(
                    execute_tool("get_user_dynamics", arguments)
                )
                self.assertEqual(
                    result,
                    {"ok": False, "error": "INVALID_TOOL_ARGUMENTS"},
                )

    def test_maps_user_dynamics_domain_error_to_stable_code(self) -> None:
        with patch(
            "bili_agent_cli.agent.registry.get_user_dynamics_tool",
            new=AsyncMock(
                side_effect=UserDynamicsError(
                    "UP主动态接口错误 -352: 风控校验失败",
                    status=412,
                    code=-352,
                )
            ),
        ):
            result = asyncio.run(
                execute_tool("get_user_dynamics", {"user_mid": "456"})
            )

        self.assertEqual(
            result,
            {
                "ok": False,
                "error": "USER_DYNAMICS_FETCH_ERROR",
                "details": {
                    "upstream_code": -352,
                    "upstream_message": (
                        "UP主动态接口错误 -352: 风控校验失败"
                    ),
                    "http_status": 412,
                },
            },
        )

    def test_rejects_invalid_user_dynamics_result(self) -> None:
        definition = TOOL_REGISTRY["get_user_dynamics"]
        invalid_definition = replace(
            definition,
            executor=AsyncMock(return_value={"user_mid": "456", "items": []}),
        )
        with patch.dict(
            TOOL_REGISTRY,
            {"get_user_dynamics": invalid_definition},
        ):
            result = asyncio.run(
                execute_tool("get_user_dynamics", {"user_mid": "456"})
            )

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_TOOL_RESULT"},
        )

    def test_rejects_invalid_user_dynamics_projection(self) -> None:
        definition = TOOL_REGISTRY["get_user_dynamics"]

        def invalid_projector(result):
            return LlmUserDynamicsResult.model_validate({})

        invalid_definition = replace(
            definition,
            executor=AsyncMock(return_value=self._user_dynamics_response()),
            result_projector=invalid_projector,
        )
        with patch.dict(
            TOOL_REGISTRY,
            {"get_user_dynamics": invalid_definition},
        ):
            result = asyncio.run(
                execute_tool("get_user_dynamics", {"user_mid": "456"})
            )

        self.assertEqual(
            result,
            {"ok": False, "error": "INVALID_LLM_TOOL_RESULT"},
        )


if __name__ == "__main__":
    unittest.main()
