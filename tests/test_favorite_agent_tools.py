from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bili_agent_cli.agent.context.models import (
    ConversationSession,
    PendingFavoriteSave,
)
from bili_agent_cli.agent.executor import execute_tool
from bili_agent_cli.agent.models import AgentSource
from bili_agent_cli.agent.tool_context import ToolExecutionContext
from bili_agent_cli.bilibili.favorites import FavoriteWriteError
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderPrivacy,
    FavoriteSavePlan,
    FavoriteSaveResponse,
    FavoriteSaveStatus,
    FavoriteSaveTargetFolder,
    FavoriteSaveVideo,
)


class FavoriteAgentToolsTest(unittest.TestCase):
    def _session(self) -> ConversationSession:
        now = datetime.now(timezone.utc)
        return ConversationSession(
            id=uuid4(),
            created_at=now,
            updated_at=now,
        )

    def _plan(self) -> FavoriteSavePlan:
        return FavoriteSavePlan(
            folder_title="UP 主动态",
            existing_folder_id=None,
            will_create_folder=True,
            privacy=FavoriteFolderPrivacy.PRIVATE,
            videos=[
                FavoriteSaveVideo(
                    bvid="BV1trusted",
                    aid="42",
                    title="可信视频",
                )
            ],
        )

    def _context(
        self,
        session: ConversationSession,
        turn_id=None,
    ) -> ToolExecutionContext:
        return ToolExecutionContext(
            session=session,
            current_turn_id=turn_id or uuid4(),
            trusted_sources=[
                AgentSource(
                    bvid="BV1trusted",
                    title="可信视频",
                    source_tools=["get_user_dynamics"],
                )
            ],
        )

    def test_prepare_accepts_only_trusted_sources_and_persists_plan(self) -> None:
        session = self._session()
        context = self._context(session)

        with (
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.load_profile",
                return_value={
                    "SESSDATA": "session",
                    "bili_jct": "csrf",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.prepare_favorite_save",
                new=AsyncMock(return_value=self._plan()),
            ) as prepare_mock,
        ):
            result = asyncio.run(
                execute_tool(
                    "prepare_save_videos_to_favorite_folder",
                    {
                        "folder_title": " UP 主动态 ",
                        "source_ids": ["bilibili:video:BV1trusted"],
                    },
                    context,
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["folder_title"], "UP 主动态")
        self.assertEqual(result["data"]["privacy"], "private")
        self.assertEqual(
            result["data"]["videos"][0]["source_id"],
            "bilibili:video:BV1trusted",
        )
        self.assertIn("(UTC+8)", result["data"]["expires_at"])
        self.assertIsNotNone(session.pending_favorite_save)
        self.assertEqual(
            session.pending_favorite_save.prepared_turn_id,
            context.current_turn_id,
        )
        request = prepare_mock.await_args.args[0]
        self.assertEqual(request.bvids, ["BV1trusted"])

    def test_prepare_rejects_source_not_in_conversation_evidence(self) -> None:
        session = self._session()
        context = ToolExecutionContext(
            session=session,
            current_turn_id=uuid4(),
            trusted_sources=[],
        )

        result = asyncio.run(
            execute_tool(
                "prepare_save_videos_to_favorite_folder",
                {
                    "folder_title": "UP 主动态",
                    "source_ids": ["bilibili:video:BV1trusted"],
                },
                context,
            )
        )

        self.assertEqual(
            result,
            {"ok": False, "error": "UNTRUSTED_VIDEO_SOURCES"},
        )
        self.assertIsNone(session.pending_favorite_save)

    def test_commit_rejects_confirmation_from_same_user_turn(self) -> None:
        session = self._session()
        turn_id = uuid4()
        confirmation_id = uuid4()
        now = datetime.now(timezone.utc)
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=confirmation_id,
            prepared_turn_id=turn_id,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            plan=self._plan(),
        )

        result = asyncio.run(
            execute_tool(
                "commit_save_videos_to_favorite_folder",
                {"confirmation_id": str(confirmation_id)},
                self._context(session, turn_id),
            )
        )

        self.assertEqual(
            result,
            {
                "ok": False,
                "error": "FAVORITE_CONFIRMATION_REQUIRES_NEW_TURN",
            },
        )
        self.assertIsNotNone(session.pending_favorite_save)

    def test_commit_on_new_turn_executes_bound_plan_and_consumes_token(self) -> None:
        session = self._session()
        prepared_turn_id = uuid4()
        confirmation_id = uuid4()
        now = datetime.now(timezone.utc)
        plan = self._plan()
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=confirmation_id,
            prepared_turn_id=prepared_turn_id,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            plan=plan,
        )
        response = FavoriteSaveResponse(
            status=FavoriteSaveStatus.COMPLETED,
            folder=FavoriteSaveTargetFolder(
                id="2002",
                title="UP 主动态",
                created=True,
            ),
            videos=plan.videos,
            requested_count=1,
            added_count=1,
            retryable=False,
        )

        with (
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.load_profile",
                return_value={
                    "SESSDATA": "session",
                    "bili_jct": "csrf",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.execute_favorite_save",
                new=AsyncMock(return_value=response),
            ) as execute_mock,
        ):
            result = asyncio.run(
                execute_tool(
                    "commit_save_videos_to_favorite_folder",
                    {"confirmation_id": str(confirmation_id)},
                    self._context(session, uuid4()),
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "completed")
        self.assertEqual(
            result["data"]["videos"][0]["source_id"],
            "bilibili:video:BV1trusted",
        )
        self.assertIsNone(session.pending_favorite_save)
        self.assertIs(execute_mock.await_args.args[0], plan)
        self.assertEqual(execute_mock.await_args.args[3], "SESSDATA=session; bili_jct=csrf")
        self.assertEqual(execute_mock.await_args.args[4], "csrf")

    def test_commit_rejects_expired_or_wrong_confirmation(self) -> None:
        session = self._session()
        now = datetime.now(timezone.utc)
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=uuid4(),
            prepared_turn_id=uuid4(),
            created_at=now - timedelta(hours=1),
            expires_at=now - timedelta(minutes=1),
            plan=self._plan(),
        )

        result = asyncio.run(
            execute_tool(
                "commit_save_videos_to_favorite_folder",
                {"confirmation_id": str(uuid4())},
                self._context(session),
            )
        )

        self.assertEqual(
            result,
            {
                "ok": False,
                "error": "INVALID_OR_EXPIRED_FAVORITE_CONFIRMATION",
            },
        )
        self.assertIsNone(session.pending_favorite_save)

    def test_partial_commit_keeps_token_and_binds_created_folder(self) -> None:
        session = self._session()
        confirmation_id = uuid4()
        now = datetime.now(timezone.utc)
        plan = self._plan()
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=confirmation_id,
            prepared_turn_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            plan=plan,
        )
        response = FavoriteSaveResponse(
            status=FavoriteSaveStatus.PARTIAL,
            folder=FavoriteSaveTargetFolder(
                id="2002",
                title="UP 主动态",
                created=True,
            ),
            videos=plan.videos,
            retry_videos=plan.videos,
            requested_count=1,
            added_count=0,
            retryable=True,
        )

        with (
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.load_profile",
                return_value={
                    "SESSDATA": "session",
                    "bili_jct": "csrf",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.execute_favorite_save",
                new=AsyncMock(return_value=response),
            ),
        ):
            result = asyncio.run(
                execute_tool(
                    "commit_save_videos_to_favorite_folder",
                    {"confirmation_id": str(confirmation_id)},
                    self._context(session),
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "partial")
        self.assertIsNotNone(session.pending_favorite_save)
        self.assertEqual(
            session.pending_favorite_save.plan.existing_folder_id,
            "2002",
        )
        self.assertFalse(session.pending_favorite_save.plan.will_create_folder)
        self.assertEqual(
            session.pending_favorite_save.plan.videos,
            plan.videos,
        )

    def test_commit_exposes_safe_upstream_error_details(self) -> None:
        session = self._session()
        confirmation_id = uuid4()
        now = datetime.now(timezone.utc)
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=confirmation_id,
            prepared_turn_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            plan=self._plan(),
        )

        with (
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.load_profile",
                return_value={
                    "SESSDATA": "session",
                    "bili_jct": "csrf",
                    "DedeUserID": "123",
                },
            ),
            patch(
                "bili_agent_cli.agent.tools.bili.save_favorites.execute_favorite_save",
                new=AsyncMock(
                    side_effect=FavoriteWriteError(
                        "批量收藏视频接口错误 -400: 请求错误",
                        code=-400,
                    )
                ),
            ),
        ):
            result = asyncio.run(
                execute_tool(
                    "commit_save_videos_to_favorite_folder",
                    {"confirmation_id": str(confirmation_id)},
                    self._context(session),
                )
            )

        self.assertEqual(result["error"], "FAVORITE_SAVE_ERROR")
        self.assertEqual(result["details"]["upstream_code"], -400)
        self.assertIn("请求错误", result["details"]["upstream_message"])
        self.assertFalse(result["details"]["outcome_unknown"])
        self.assertIsNotNone(session.pending_favorite_save)


if __name__ == "__main__":
    unittest.main()
