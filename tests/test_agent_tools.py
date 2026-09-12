from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from bili_agent_cli.agent.executor import execute_tool
from bili_agent_cli.agent.models import AgentSource
from bili_agent_cli.agent.registry import TOOL_REGISTRY, build_tool_schemas
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideo,
    FavoriteVideoAuthor,
)
from bili_agent_cli.schemas.common import VideoStats


class AgentToolsTest(unittest.TestCase):
    def test_content_tools_are_registered(self) -> None:
        self.assertEqual(
            set(TOOL_REGISTRY),
            {
                "get_following_feed",
                "get_favorite_folders",
                "get_favorite_folder_videos",
                "get_watch_later",
                "search_videos",
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
        self.assertIn("submit_agent_answer", schemas)
        self.assertIn(
            "source_ids",
            schemas["submit_agent_answer"]["parameters"]["properties"],
        )

    def test_agent_source_keeps_old_fields_and_derives_source_id(self) -> None:
        source = AgentSource(bvid="BV1compatible", title="兼容来源")

        self.assertEqual(source.source_id, "bilibili:video:BV1compatible")
        self.assertEqual(source.source_tools, [])

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


if __name__ == "__main__":
    unittest.main()
