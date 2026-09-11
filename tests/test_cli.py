from __future__ import annotations

import argparse
import asyncio
import unittest
from io import StringIO
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from bili_agent_cli.agent.models import AgentRunResponse
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


if __name__ == "__main__":
    unittest.main()
