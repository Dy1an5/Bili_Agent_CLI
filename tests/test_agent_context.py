from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bili_agent_cli.agent.agent_loop import run_agent
from bili_agent_cli.agent.context import (
    ContextManager,
    ContextSettings,
    FileSessionStore,
)
from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryScope,
    MemorySourceType,
)
from bili_agent_cli.agent.memory.store import (
    MemoryStorageError,
    MemoryStore,
)
from bili_agent_cli.agent.models import AgentMemoryStatus
from bili_agent_cli.agent.deepseek.errors import ProviderTimeoutError


class AgentContextTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.session_store = FileSessionStore(
            directory=Path(self.temporary_directory.name) / "conversations",
            ttl_seconds=3_600,
            capacity=100,
        )
        self.store_patch = patch(
            "bili_agent_cli.agent.agent_loop.session_store",
            new=self.session_store,
        )
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

        self.memory_store = MemoryStore(
            Path(self.temporary_directory.name) / "memory.db"
        )
        self.memory_store_patch = patch(
            "bili_agent_cli.agent.agent_loop.memory_store",
            new=self.memory_store,
        )
        self.memory_store_patch.start()
        self.addCleanup(self.memory_store_patch.stop)

        self.memory_extraction = AsyncMock(
            return_value=MemoryExtraction(candidates=[])
        )
        self.memory_extraction_patch = patch(
            "bili_agent_cli.agent.agent_loop.create_memory_extraction",
            new=self.memory_extraction,
        )
        self.memory_extraction_patch.start()
        self.addCleanup(self.memory_extraction_patch.stop)

    async def test_relevant_memory_is_injected_into_system_prompt(self) -> None:
        remembered = self.memory_store.remember(
            MemoryCandidate(
                key="recommendation.f1.level",
                kind="constraint",
                content="推荐 F1 时不要提供入门规则视频",
                topics=["f1"],
                confidence=1.0,
                evidence_quote="以后推荐 F1 时不要提供入门规则视频",
                durability=MemoryDurability.EXPLICIT,
                scope=MemoryScope.TOPIC,
                scope_value="f1",
            ),
            source_type=MemorySourceType.USER,
            source_ref=None,
        )
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "回答"}
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            await run_agent("推荐 F1 视频")

        messages = create_message.await_args.kwargs["messages"]
        system_content = messages[0]["content"]
        self.assertIn("<long_term_memories>", system_content)
        self.assertIn(str(remembered.id), system_content)
        self.assertIn("不要提供入门规则视频", system_content)
        self.assertEqual(
            messages[-1],
            {"role": "user", "content": "推荐 F1 视频"},
        )

    async def test_memory_failure_does_not_block_agent(self) -> None:
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "正常回答"}
        )

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.retrieve_memories",
                side_effect=MemoryStorageError("database unavailable"),
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
        ):
            response = await run_agent("普通问题")

        self.assertEqual(response.answer, "正常回答")
        messages = create_message.await_args.kwargs["messages"]
        self.assertNotIn("<long_term_memories>", messages[0]["content"])
        self.assertEqual(
            messages[-1],
            {"role": "user", "content": "普通问题"},
        )

    async def test_completed_turn_is_extracted_into_memory_store(self) -> None:
        candidate = MemoryCandidate(
            key="response.length",
            kind="constraint",
            content="以后回答保持简短",
            topics=["回答风格"],
            confidence=1.0,
            evidence_quote="以后回答保持简短",
            durability=MemoryDurability.EXPLICIT,
            scope=MemoryScope.GLOBAL,
            scope_value=None,
        )
        self.memory_extraction.return_value = MemoryExtraction(
            candidates=[candidate]
        )
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "已经记住"}
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("以后回答保持简短")

        memories = self.memory_store.list_memories()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].key, "response.length")
        self.assertEqual(
            memories[0].source_type,
            MemorySourceType.CONVERSATION,
        )
        self.assertEqual(response.memory.status, AgentMemoryStatus.SAVED)
        self.assertEqual(response.memory.saved_count, 1)

        session = await self.session_store.get(response.session_id)
        completed_turn = session.turns[-1]
        self.assertEqual(completed_turn.status, "completed")
        self.assertEqual(
            memories[0].source_ref,
            f"session:{session.id}:turn:{completed_turn.id}",
        )

        self.memory_extraction.assert_awaited_once_with("以后回答保持简短")

    async def test_extraction_failure_keeps_completed_answer(self) -> None:
        self.memory_extraction.side_effect = ProviderTimeoutError(
            "memory extraction timeout"
        )
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "回答仍然成功"}
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("以后回答保持简短")

        self.assertEqual(response.answer, "回答仍然成功")
        self.assertEqual(
            response.memory.status,
            AgentMemoryStatus.EXTRACTION_FAILED,
        )
        self.assertEqual(
            response.memory.error_code,
            "ProviderTimeoutError",
        )
        self.assertEqual(self.memory_store.list_memories(), [])
        session = await self.session_store.get(response.session_id)
        self.assertEqual(session.turns[-1].status, "completed")
        self.assertEqual(
            session.turns[-1].assistant_content,
            "回答仍然成功",
        )

    async def test_empty_extraction_is_reported(self) -> None:
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "普通回答"}
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("今天天气不错")

        self.assertEqual(
            response.memory.status,
            AgentMemoryStatus.NO_CANDIDATES,
        )
        self.assertEqual(response.memory.saved_count, 0)
        self.assertIsNone(response.memory.error_code)
        self.memory_extraction.assert_not_awaited()

    async def test_one_off_preference_candidate_stays_pending(self) -> None:
        self.memory_extraction.return_value = MemoryExtraction(
            candidates=[
                MemoryCandidate(
                    key="recommendation.video.creator_tier",
                    kind="preference",
                    content="视频推荐优先头部科技 UP 主",
                    topics=["科技"],
                    confidence=0.8,
                    evidence_quote="筛选出头部科技UP的视频",
                    durability=MemoryDurability.EXPLICIT,
                    scope=MemoryScope.INTENT,
                    scope_value="video_recommendation",
                )
            ]
        )
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "筛选结果"}
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("筛选出头部科技UP的视频")

        self.assertEqual(response.memory.status, AgentMemoryStatus.PENDING)
        self.assertEqual(response.memory.pending_saved_count, 1)
        self.assertEqual(self.memory_store.list_memories(), [])

    async def test_storage_failure_is_reported(self) -> None:
        self.memory_extraction.return_value = MemoryExtraction(
            candidates=[
                MemoryCandidate(
                    key="response.length",
                    kind="constraint",
                    content="以后回答保持简短",
                    topics=["回答风格"],
                    confidence=1.0,
                    evidence_quote="以后回答保持简短",
                    durability=MemoryDurability.EXPLICIT,
                    scope=MemoryScope.GLOBAL,
                    scope_value=None,
                )
            ]
        )
        create_message = AsyncMock(
            return_value={"role": "assistant", "content": "已处理"}
        )

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch.object(
                self.memory_store,
                "observe_many",
                side_effect=MemoryStorageError("database unavailable"),
            ),
        ):
            response = await run_agent("以后回答保持简短")

        self.assertEqual(
            response.memory.status,
            AgentMemoryStatus.STORAGE_FAILED,
        )
        self.assertEqual(response.memory.saved_count, 0)
        self.assertEqual(response.memory.error_code, "MemoryStorageError")

    async def test_second_run_receives_first_completed_turn(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {"role": "assistant", "content": "第一轮答案"},
                {"role": "assistant", "content": "第二轮答案"},
            ]
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            first = await run_agent("第一轮问题")
            await run_agent("第二轮问题", first.session_id)

        second_messages = create_message.await_args_list[1].kwargs["messages"]
        self.assertEqual(
            [message["role"] for message in second_messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(second_messages[1]["content"], "第一轮问题")
        self.assertEqual(second_messages[2]["content"], "第一轮答案")

    async def test_agent_reports_compaction(self) -> None:
        manager = ContextManager(
            ContextSettings(
                max_input_units=5_000,
                summarize_at_units=100,
                recent_turns=1,
                summary_max_tokens=100,
                max_tool_result_units=1_000,
            )
        )
        create_message = AsyncMock(
            side_effect=[
                {"role": "assistant", "content": "一" * 80},
                {"role": "assistant", "content": "二" * 80},
                {"role": "assistant", "content": "压缩后回答"},
            ]
        )
        summarize = AsyncMock(return_value="历史摘要")

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.context_manager",
                new=manager,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.create_conversation_summary",
                new=summarize,
            ),
        ):
            first = await run_agent("第一轮" * 20)
            await run_agent("第二轮" * 20, first.session_id)
            third = await run_agent("第三轮", first.session_id)

        self.assertTrue(third.context_compacted)
        summarize.assert_awaited_once()
        third_messages = create_message.await_args_list[2].kwargs["messages"]
        self.assertIn("历史摘要", third_messages[0]["content"])

    async def test_agent_stores_visible_evidence_and_selected_sources(self) -> None:
        manager = ContextManager(
            ContextSettings(
                max_input_units=20_000,
                summarize_at_units=15_000,
                recent_turns=1,
                summary_max_tokens=100,
                max_tool_result_units=700,
            )
        )
        tool_result = {
            "ok": True,
            "data": {
                "videos": [
                    {
                        "source_id": f"bilibili:video:BV{index}",
                        "bvid": f"BV{index}",
                        "title": f"视频 {index}",
                        "description": "内容" * 80,
                    }
                    for index in range(8)
                ]
            },
        }
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "search_videos",
                                "arguments": '{"keyword":"Python"}',
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "submit-1",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": "完成",
                                        "source_ids": [
                                            "bilibili:video:BV0"
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
            ]
        )

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.context_manager",
                new=manager,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value=tool_result),
            ),
        ):
            response = await run_agent("搜索 Python")

        self.assertEqual([source.bvid for source in response.sources], ["BV0"])
        second_messages = create_message.await_args_list[1].kwargs["messages"]
        tool_message = next(
            message
            for message in reversed(second_messages)
            if message["role"] == "tool"
        )
        model_tool_result = json.loads(tool_message["content"])
        self.assertTrue(model_tool_result["context_truncation"]["truncated"])
        self.assertNotIn("BV7", json.dumps(model_tool_result))
        session = await self.session_store.get(response.session_id)
        self.assertEqual(
            session.turns[-1].evidence_batches[0].result,
            model_tool_result,
        )

    async def test_multiple_tools_create_batches_and_merge_source_tools(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "search-call",
                            "function": {
                                "name": "search_videos",
                                "arguments": '{"keyword":"F1"}',
                            },
                        },
                        {
                            "id": "later-call",
                            "function": {
                                "name": "get_watch_later",
                                "arguments": "{}",
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "submit-call",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": "两个工具都找到了同一视频",
                                        "source_ids": [
                                            "bilibili:video:BV1shared"
                                        ],
                                    }
                                ),
                            },
                        }
                    ],
                },
            ]
        )
        execute = AsyncMock(
            side_effect=[
                {
                    "ok": True,
                    "data": {
                        "videos": [
                            {
                                "source_id": "bilibili:video:BV1shared",
                                "bvid": "BV1shared",
                                "title": "搜索结果",
                            }
                        ],
                        "page": 1,
                        "page_size": 20,
                        "has_more": False,
                    },
                },
                {
                    "ok": True,
                    "data": {
                        "videos": [
                            {
                                "source_id": "bilibili:video:BV1shared",
                                "bvid": "BV1shared",
                                "cid": "123",
                                "title": "稍后再看结果",
                            }
                        ],
                        "page": 1,
                        "page_size": 20,
                        "has_more": False,
                    },
                },
            ]
        )

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=execute,
            ),
        ):
            response = await run_agent("从多个位置找 F1")

        self.assertEqual(len(response.sources), 1)
        self.assertEqual(
            response.sources[0].source_tools,
            ["search_videos", "get_watch_later"],
        )
        self.assertEqual(response.sources[0].cid, "123")
        session = await self.session_store.get(response.session_id)
        self.assertEqual(
            [batch.tool_name for batch in session.turns[-1].evidence_batches],
            ["search_videos", "get_watch_later"],
        )

    async def test_unknown_submitted_source_is_rejected_then_retried(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "bad-submit",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": "错误来源",
                                        "source_ids": [
                                            "bilibili:video:BVfake"
                                        ],
                                    }
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "good-submit",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {"answer": "已修正", "source_ids": []}
                                ),
                            },
                        }
                    ],
                },
            ]
        )

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("普通问题")

        retry_messages = create_message.await_args_list[1].kwargs["messages"]
        error_message = next(
            message
            for message in reversed(retry_messages)
            if message["role"] == "tool"
        )
        error = json.loads(error_message["content"])
        self.assertEqual(error["error"], "UNKNOWN_SOURCE_IDS")
        self.assertEqual(response.answer, "已修正")
        self.assertEqual(response.sources, [])

    async def test_final_answer_must_be_separate_from_data_tools(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "early-submit",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {"answer": "过早提交", "source_ids": []}
                                ),
                            },
                        },
                        {
                            "id": "search-call",
                            "function": {
                                "name": "search_videos",
                                "arguments": '{"keyword":"F1"}',
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "final-submit",
                            "function": {
                                "name": "submit_agent_answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": "正确提交",
                                        "source_ids": [
                                            "bilibili:video:BV1result"
                                        ],
                                    }
                                ),
                            },
                        }
                    ],
                },
            ]
        )
        tool_result = {
            "ok": True,
            "data": {
                "videos": [
                    {
                        "source_id": "bilibili:video:BV1result",
                        "bvid": "BV1result",
                        "title": "工具结果",
                    }
                ],
                "page": 1,
                "page_size": 20,
                "has_more": False,
            },
        }

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value=tool_result),
            ),
        ):
            response = await run_agent("搜索并回答")

        retry_messages = create_message.await_args_list[1].kwargs["messages"]
        tool_errors = [
            json.loads(message["content"])
            for message in retry_messages
            if message["role"] == "tool"
        ]
        self.assertTrue(
            any(
                result.get("error") == "FINAL_ANSWER_MUST_BE_SUBMITTED_ALONE"
                for result in tool_errors
            )
        )
        self.assertEqual(response.answer, "正确提交")
        self.assertEqual(
            [source.bvid for source in response.sources],
            ["BV1result"],
        )
        self.assertEqual(
            [step.tool for step in response.trace],
            ["search_videos"],
        )

    async def test_plain_answer_falls_back_to_trusted_bvids(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "search-call",
                            "function": {
                                "name": "search_videos",
                                "arguments": '{"keyword":"F1"}',
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "推荐 BV1trusted123，同时忽略 BV1invented99。",
                },
            ]
        )
        tool_result = {
            "ok": True,
            "data": {
                "videos": [
                    {
                        "source_id": "bilibili:video:BV1trusted123",
                        "bvid": "BV1trusted123",
                        "title": "可信视频",
                    }
                ],
                "page": 1,
                "page_size": 20,
                "has_more": False,
            },
        }

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value=tool_result),
            ),
        ):
            response = await run_agent("搜索 F1")

        self.assertEqual(
            [source.bvid for source in response.sources],
            ["BV1trusted123"],
        )

    async def test_next_turn_receives_following_pagination_state(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "get_following_feed",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "还有下一页，要继续吗？"},
                {"role": "assistant", "content": "继续后的回答"},
            ]
        )
        tool_result = {
            "ok": True,
            "data": {
                "items": [],
                "has_more": True,
                "next_offset": "opaque-next-offset",
            },
        }

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value=tool_result),
            ),
        ):
            first = await run_agent("查看关注动态")
            await run_agent("继续", first.session_id)

        next_turn_messages = create_message.await_args_list[2].kwargs["messages"]
        previous_answer = next_turn_messages[-2]["content"]
        self.assertIn("<pagination_state>", previous_answer)
        self.assertIn("opaque-next-offset", previous_answer)

    async def test_interrupted_turn_keeps_tool_evidence_for_retry(self) -> None:
        session = await self.session_store.create()
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-interrupted",
                            "function": {
                                "name": "get_following_feed",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                RuntimeError("provider failed after tool call"),
                {"role": "assistant", "content": "已从中断位置继续"},
            ]
        )
        tool_result = {
            "ok": True,
            "data": {
                "items": [],
                "has_more": True,
                "next_offset": "saved-offset",
            },
        }

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value=tool_result),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await run_agent("查看动态", session.id)

            interrupted = await self.session_store.get(session.id)
            self.assertIsNotNone(interrupted.pending_turn)
            self.assertEqual(interrupted.pending_turn.status, "interrupted")
            self.assertEqual(len(interrupted.pending_turn.evidence_batches), 1)
            self.assertEqual(
                interrupted.pending_turn.pagination_states[0].next_arguments,
                {"offset": "saved-offset"},
            )
            reloaded_store = FileSessionStore(
                directory=self.session_store.directory,
                ttl_seconds=3_600,
                capacity=100,
            )
            reloaded = await reloaded_store.get(session.id)
            self.assertIsNotNone(reloaded.pending_turn)
            self.assertEqual(
                reloaded.pending_turn.pagination_states[0].next_arguments,
                {"offset": "saved-offset"},
            )

            await run_agent("继续", session.id)

        retry_messages = create_message.await_args_list[2].kwargs["messages"]
        self.assertIn("saved-offset", retry_messages[-2]["content"])
        completed = await self.session_store.get(session.id)
        self.assertIsNone(completed.pending_turn)
        self.assertEqual(
            [turn.status for turn in completed.turns[-2:]],
            ["interrupted", "completed"],
        )


if __name__ == "__main__":
    unittest.main()
