from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from bili_agent_cli.agent.executor import execute_tool
from bili_agent_cli.agent.registry import TOOL_REGISTRY, build_tool_schemas
from bili_agent_cli.schemas.favorites import (
    FavoriteFolder,
    FavoriteFolderListResponse,
)


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


if __name__ == "__main__":
    unittest.main()
