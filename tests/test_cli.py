from __future__ import annotations

import argparse
import asyncio
import unittest
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from bili_agent_cli.agent.models import AgentRunResponse
from bili_agent_cli.agent.context import (
    AgentEvidenceBatch,
    ConversationSession,
    ConversationTurn,
)
from bili_agent_cli.agent.deepseek.errors import ProviderTimeoutError
from bili_agent_cli.cli.agent import run as run_agent_chat
from bili_agent_cli.cli.main import _create_parser


class CliParserTest(unittest.TestCase):
    def test_existing_commands_are_registered(self) -> None:
        parser = _create_parser()

        login = parser.parse_args(["login"])
        profile = parser.parse_args(["profile"])
        get = parser.parse_args(["get", "/x/web-interface/nav"])
        agent = parser.parse_args(["agent"])

        self.assertEqual(login.timeout, 180)
        self.assertEqual(profile.command, "profile")
        self.assertEqual(get.path, "/x/web-interface/nav")
        self.assertEqual(agent.command, "agent")
        self.assertTrue(callable(login.handler))
        self.assertTrue(callable(profile.handler))
        self.assertTrue(callable(get.handler))
        self.assertTrue(callable(agent.handler))

    def test_get_command_parses_repeated_parameters(self) -> None:
        arguments = _create_parser().parse_args(
            [
                "get",
                "/x/test",
                "--param",
                "page=1",
                "--param",
                "keyword=Python",
            ]
        )

        self.assertEqual(
            arguments.param,
            [("page", "1"), ("keyword", "Python")],
        )


class AgentCliTest(unittest.TestCase):
    @staticmethod
    def _response(answer: str, session_id: UUID) -> AgentRunResponse:
        return AgentRunResponse(
            answer=answer,
            session_id=session_id,
        )

    def test_chat_reuses_session_id(self) -> None:
        session_id = uuid4()
        model = AsyncMock(
            side_effect=[
                self._response("第一轮答案", session_id),
                self._response("第二轮答案", session_id),
            ]
        )

        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                side_effect=["第一轮", "第二轮", "/exit"],
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", new_callable=StringIO),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertEqual(model.await_args_list[0].args, ("第一轮", None))
        self.assertEqual(model.await_args_list[1].args, ("第二轮", session_id))

    def test_new_command_resets_session_id(self) -> None:
        first_session_id = uuid4()
        second_session_id = uuid4()
        model = AsyncMock(
            side_effect=[
                self._response("第一轮答案", first_session_id),
                self._response("新会话答案", second_session_id),
            ]
        )

        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                side_effect=["第一轮", "/new", "新会话", "/exit"],
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", new_callable=StringIO),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertEqual(model.await_args_list[0].args, ("第一轮", None))
        self.assertEqual(model.await_args_list[1].args, ("新会话", None))

    def test_context_command_prints_persisted_session(self) -> None:
        session_id = uuid4()
        now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        session = ConversationSession(
            id=session_id,
            created_at=now,
            updated_at=now,
            summary="更早对话的摘要",
            turns=[
                ConversationTurn(
                    user_content="第一轮问题",
                    assistant_content="第一轮答案",
                    evidence_batches=[
                        AgentEvidenceBatch(
                            tool_call_id="call-1",
                            tool_name="search_videos",
                            arguments={"keyword": "F1"},
                            result={"ok": True, "data": {"videos": []}},
                        )
                    ],
                )
            ],
        )
        model = AsyncMock(
            return_value=self._response("第一轮答案", session_id)
        )
        output = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                side_effect=["第一轮问题", "/context", "/exit"],
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch(
                "bili_agent_cli.cli.agent.session_store.get",
                new=AsyncMock(return_value=session),
            ),
            patch("sys.stdout", output),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        rendered = output.getvalue()
        self.assertIn(str(session_id), rendered)
        self.assertIn("更早对话的摘要", rendered)
        self.assertIn("第一轮问题", rendered)
        self.assertIn("evidence_batches", rendered)
        self.assertIn("search_videos", rendered)
        self.assertIn("pending_turn", rendered)

    def test_context_command_before_first_turn_explains_empty_state(self) -> None:
        output = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                side_effect=["/context", "/exit"],
            ),
            patch("sys.stdout", output),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertIn("当前还没有已完成的对话", output.getvalue())

    def test_chat_keeps_running_after_recoverable_turn_error(self) -> None:
        session_id = uuid4()
        model = AsyncMock(
            side_effect=[
                self._response("第一轮答案", session_id),
                ProviderTimeoutError("模型请求超时"),
                self._response("重试成功", session_id),
            ]
        )
        output = StringIO()
        errors = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                side_effect=["第一轮", "继续", "重试", "/exit"],
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertEqual(model.await_count, 3)
        self.assertEqual(model.await_args_list[1].args, ("继续", session_id))
        self.assertEqual(model.await_args_list[2].args, ("重试", session_id))
        self.assertIn("模型请求超时", errors.getvalue())
        self.assertIn("重试成功", output.getvalue())


if __name__ == "__main__":
    unittest.main()
