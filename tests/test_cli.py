from __future__ import annotations

import argparse
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from bili_agent_cli.agent.models import (
    AgentMemoryResult,
    AgentMemoryStatus,
    AgentRunResponse,
)
from bili_agent_cli.agent.context import (
    AgentEvidenceBatch,
    ConversationSession,
    ConversationTurn,
)
from bili_agent_cli.agent.deepseek.errors import ProviderTimeoutError
from bili_agent_cli.agent.memory import (
    MemoryCandidate,
    MemoryDurability,
    MemoryScope,
    MemorySourceType,
    MemoryState,
    MemoryStore,
)
from bili_agent_cli.cli.agent import _handle_memory_command, _read_task
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
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(side_effect=["第一轮", "第二轮", "/exit"]),
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", new_callable=StringIO),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertEqual(model.await_args_list[0].args, ("第一轮", None))
        self.assertEqual(model.await_args_list[1].args, ("第二轮", session_id))

    def test_chat_reports_memory_save_result(self) -> None:
        session_id = uuid4()
        model = AsyncMock(
            return_value=AgentRunResponse(
                answer="已经处理",
                session_id=session_id,
                memory=AgentMemoryResult(
                    status=AgentMemoryStatus.SAVED,
                    saved_count=1,
                    active_saved_count=1,
                ),
            )
        )
        output = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(side_effect=["记住偏好", "/exit"]),
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", output),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertIn("记忆> 已更新长期记忆：新增 1 条。", output.getvalue())

    def test_chat_reports_memory_extraction_failure(self) -> None:
        session_id = uuid4()
        model = AsyncMock(
            return_value=AgentRunResponse(
                answer="主回答仍成功",
                session_id=session_id,
                memory=AgentMemoryResult(
                    status=AgentMemoryStatus.EXTRACTION_FAILED,
                    error_code="ProviderResponseError",
                ),
            )
        )
        errors = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(side_effect=["记住偏好", "/exit"]),
            ),
            patch("bili_agent_cli.cli.agent.run_agent", new=model),
            patch("sys.stdout", new_callable=StringIO),
            patch("sys.stderr", errors),
        ):
            asyncio.run(run_agent_chat(argparse.Namespace()))

        self.assertIn(
            "记忆> 保存失败（ProviderResponseError）。",
            errors.getvalue(),
        )

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
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(
                    side_effect=["第一轮", "/new", "新会话", "/exit"]
                ),
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
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(
                    side_effect=["第一轮问题", "/context", "/exit"]
                ),
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
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(side_effect=["/context", "/exit"]),
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
                "bili_agent_cli.cli.agent._read_task",
                new=AsyncMock(
                    side_effect=["第一轮", "继续", "重试", "/exit"]
                ),
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


class MemoryCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = MemoryStore(
            Path(self.temporary_directory.name) / "memory.db"
        )
        self.store_patch = patch(
            "bili_agent_cli.cli.agent.memory_store", new=self.store
        )
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def create_pending(self):
        return self.store.remember(
            MemoryCandidate(
                key="recommendation.video.links",
                kind="preference",
                content="视频推荐直接给链接",
                topics=[],
                confidence=0.8,
                evidence_quote="给网址",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
            ),
            source_type=MemorySourceType.CONVERSATION,
            source_ref="turn:1",
        )

    def test_list_confirm_show_edit_delete_restore_and_clear(self) -> None:
        pending = self.create_pending()
        output = StringIO()

        with patch("sys.stdout", output):
            _handle_memory_command("/memory pending")
            _handle_memory_command(f"/memory confirm {pending.id}")
            _handle_memory_command(f"/memory show {pending.id}")
            _handle_memory_command(
                f"/memory edit {pending.id} 视频推荐必须直接给链接"
            )
            _handle_memory_command(f"/memory delete {pending.id}")
            _handle_memory_command(f"/memory restore {pending.id}")
            _handle_memory_command("/memory clear --yes")

        rendered = output.getvalue()
        self.assertIn(str(pending.id), rendered)
        self.assertIn("已确认", rendered)
        self.assertIn("证据", rendered)
        self.assertIn("已修改并激活", rendered)
        self.assertIn("已软删除", rendered)
        self.assertIn("已恢复并激活", rendered)
        self.assertIn("已软删除 1 条", rendered)
        self.assertEqual(
            self.store.get_memory(pending.id).state,
            MemoryState.DELETED,
        )

    def test_invalid_memory_command_is_reported(self) -> None:
        errors = StringIO()
        with patch("sys.stderr", errors):
            _handle_memory_command("/memory clear")

        self.assertIn("操作失败", errors.getvalue())


class ReadTaskTest(unittest.IsolatedAsyncioTestCase):
    """输入接缝：真终端走 prompt_toolkit，其余情况退回内置 input。"""

    def setUp(self) -> None:
        for target, value in (
            ("bili_agent_cli.cli.agent._prompt_session", None),
            ("bili_agent_cli.cli.agent._enhanced_input_disabled", False),
        ):
            item = patch(target, new=value)
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def _stream(is_tty: bool) -> SimpleNamespace:
        return SimpleNamespace(isatty=lambda: is_tty)

    async def test_falls_back_to_input_without_tty(self) -> None:
        with (
            patch(
                "bili_agent_cli.cli.agent.input",
                return_value="看动态",
            ) as input_mock,
            patch("sys.stdin", self._stream(False)),
            patch("sys.stdout", self._stream(True)),
        ):
            result = await _read_task("你> ")

        self.assertEqual(result, "看动态")
        input_mock.assert_called_once_with("你> ")

    async def test_uses_prompt_session_on_tty(self) -> None:
        session = SimpleNamespace(
            prompt_async=AsyncMock(return_value="看动态")
        )

        with (
            patch(
                "bili_agent_cli.cli.agent._get_prompt_session",
                return_value=session,
            ),
            patch("bili_agent_cli.cli.agent.input") as input_mock,
            patch("sys.stdin", self._stream(True)),
            patch("sys.stdout", self._stream(True)),
        ):
            result = await _read_task("你> ")

        self.assertEqual(result, "看动态")
        session.prompt_async.assert_awaited_once_with("你> ")
        input_mock.assert_not_called()

    async def test_falls_back_once_when_prompt_session_fails(self) -> None:
        session = SimpleNamespace(
            prompt_async=AsyncMock(side_effect=RuntimeError("终端不可用"))
        )
        errors = StringIO()

        with (
            patch(
                "bili_agent_cli.cli.agent._get_prompt_session",
                return_value=session,
            ),
            patch(
                "bili_agent_cli.cli.agent.input",
                return_value="看动态",
            ) as input_mock,
            patch("sys.stdin", self._stream(True)),
            patch("sys.stdout", self._stream(True)),
            patch("sys.stderr", errors),
        ):
            first = await _read_task("你> ")
            second = await _read_task("你> ")

        self.assertEqual((first, second), ("看动态", "看动态"))
        self.assertEqual(input_mock.call_count, 2)
        self.assertEqual(session.prompt_async.await_count, 1)
        self.assertIn("已退回基础输入模式", errors.getvalue())

    async def test_eof_and_interrupt_propagate(self) -> None:
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                session = SimpleNamespace(
                    prompt_async=AsyncMock(side_effect=error)
                )

                with (
                    patch(
                        "bili_agent_cli.cli.agent._get_prompt_session",
                        return_value=session,
                    ),
                    patch("sys.stdin", self._stream(True)),
                    patch("sys.stdout", self._stream(True)),
                ):
                    with self.assertRaises(type(error)):
                        await _read_task("你> ")


if __name__ == "__main__":
    unittest.main()
