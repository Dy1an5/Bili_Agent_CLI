from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

from bili_agent_cli.agent.agent_loop import run_agent
from bili_agent_cli.agent.context import (
    ContextManager,
    ContextSettings,
    FileSessionStore,
)
from bili_agent_cli.agent.context.manager import (
    extract_pagination_state,
    extract_sources,
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
from bili_agent_cli.agent.models import AgentMemoryStatus, TokenUsage
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

        self.memory_extraction.assert_awaited_once_with(
            "以后回答保持简短",
            on_usage=ANY,
        )

    async def test_usage_sums_model_responses_not_parallel_tool_calls(self) -> None:
        responses = [
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
                                {"answer": "完成", "source_ids": []}
                            ),
                        },
                    }
                ],
            },
        ]
        usages = iter(
            [
                TokenUsage(input_tokens=10, output_tokens=2),
                TokenUsage(input_tokens=20, output_tokens=3),
            ]
        )

        async def create_message(*, messages, tools, on_usage=None):
            del messages, tools
            on_usage(next(usages))
            return responses.pop(0)

        with (
            patch(
                "bili_agent_cli.agent.agent_loop.create_agent_message",
                new=create_message,
            ),
            patch(
                "bili_agent_cli.agent.agent_loop.execute_tool",
                new=AsyncMock(return_value={"ok": True, "data": {}}),
            ),
        ):
            response = await run_agent("搜索 F1")

        self.assertEqual(
            response.usage,
            TokenUsage(input_tokens=30, output_tokens=5),
        )

    async def test_usage_includes_summary_agent_and_memory_calls(self) -> None:
        manager = ContextManager(
            ContextSettings(
                max_input_units=11_000,
                summarize_at_units=100,
                recent_turns=1,
                summary_max_tokens=100,
                max_tool_result_units=1_000,
            )
        )

        async def create_message(*, messages, tools, on_usage=None):
            del messages, tools
            on_usage(TokenUsage(input_tokens=5, output_tokens=1))
            return {"role": "assistant", "content": "回答" * 40}

        async def summarize(summary_input, max_tokens, on_usage=None):
            del summary_input, max_tokens
            on_usage(TokenUsage(input_tokens=11, output_tokens=2))
            return "历史摘要"

        async def extract(extraction_input, on_usage=None):
            del extraction_input
            on_usage(TokenUsage(input_tokens=13, output_tokens=3))
            return MemoryExtraction(candidates=[])

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
            patch(
                "bili_agent_cli.agent.agent_loop.create_memory_extraction",
                new=extract,
            ),
        ):
            first = await run_agent("第一轮" * 20)
            await run_agent("第二轮" * 20, first.session_id)
            third = await run_agent("以后回答保持简短", first.session_id)

        self.assertTrue(third.context_compacted)
        self.assertEqual(
            third.usage,
            TokenUsage(input_tokens=29, output_tokens=6),
        )

    async def test_usage_only_counts_agent_when_no_auxiliary_call_runs(self) -> None:
        async def create_message(*, messages, tools, on_usage=None):
            del messages, tools
            on_usage(TokenUsage(input_tokens=7, output_tokens=4))
            return {"role": "assistant", "content": "普通回答"}

        with patch(
            "bili_agent_cli.agent.agent_loop.create_agent_message",
            new=create_message,
        ):
            response = await run_agent("今天天气不错")

        self.assertEqual(
            response.usage,
            TokenUsage(input_tokens=7, output_tokens=4),
        )
        self.memory_extraction.assert_not_awaited()

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
                max_input_units=11_000,
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

    async def test_user_dynamic_video_is_available_as_validated_source(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "dynamic-call",
                            "function": {
                                "name": "get_user_dynamics",
                                "arguments": '{"user_mid":"456"}',
                            },
                        }
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
                                        "answer": "引用了转发动态中的原视频",
                                        "source_ids": [
                                            "bilibili:video:BV1dynamic"
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
                "user_mid": "456",
                "items": [
                    {
                        "dynamic_id": "102",
                        "type": "DYNAMIC_TYPE_FORWARD",
                        "author": {"mid": "456", "name": "转发者"},
                        "content": {"bvid": None, "title": None},
                        "original": {
                            "dynamic_id": "101",
                            "type": "DYNAMIC_TYPE_AV",
                            "author": {"mid": "789", "name": "原UP主"},
                            "content": {
                                "bvid": "BV1dynamic",
                                "title": "原动态视频",
                                "source_id": "bilibili:video:BV1dynamic",
                            },
                            "original": None,
                        },
                    }
                ],
                "total_count": 1,
                "skipped_count": 0,
                "pages_fetched": 2,
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
            response = await run_agent("查看指定 UP 主的全部动态")

        self.assertEqual(len(response.sources), 1)
        self.assertEqual(response.sources[0].bvid, "BV1dynamic")
        self.assertEqual(response.sources[0].title, "原动态视频")
        self.assertEqual(response.sources[0].author_name, "原UP主")
        self.assertEqual(
            response.sources[0].source_tools,
            ["get_user_dynamics"],
        )
        session = await self.session_store.get(response.session_id)
        self.assertEqual(
            session.turns[-1].evidence_batches[0].tool_name,
            "get_user_dynamics",
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

    async def test_next_turn_receives_following_users_page_state(self) -> None:
        create_message = AsyncMock(
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-users-1",
                            "function": {
                                "name": "get_following_users",
                                "arguments": (
                                    '{"page":1,"page_size":20,'
                                    '"sort":"recent"}'
                                ),
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "还有关注用户，要继续吗？"},
                {"role": "assistant", "content": "已读取下一页关注用户"},
            ]
        )
        tool_result = {
            "ok": True,
            "data": {
                "users": [],
                "total": 21,
                "page": 1,
                "page_size": 20,
                "has_more": True,
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
            first = await run_agent("查看我关注的用户")
            await run_agent("继续", first.session_id)

        next_turn_messages = create_message.await_args_list[2].kwargs["messages"]
        previous_answer = next_turn_messages[-2]["content"]
        self.assertIn("<pagination_state>", previous_answer)
        self.assertIn('"page":2', previous_answer)
        self.assertIn('"sort":"recent"', previous_answer)

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

    async def test_tool_execution_context_receives_sources_from_prior_call(self) -> None:
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
                                "arguments": '{"keyword":"动态视频"}',
                            },
                        },
                        {
                            "id": "prepare-call",
                            "function": {
                                "name": "prepare_save_videos_to_favorite_folder",
                                "arguments": json.dumps(
                                    {
                                        "folder_title": "UP 主动态",
                                        "source_ids": [
                                            "bilibili:video:BV1trusted"
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    ],
                },
                {"role": "assistant", "content": "请确认收藏计划"},
            ]
        )
        execute = AsyncMock(
            side_effect=[
                {
                    "ok": True,
                    "data": {
                        "videos": [
                            {
                                "source_id": "bilibili:video:BV1trusted",
                                "bvid": "BV1trusted",
                                "title": "可信视频",
                            }
                        ]
                    },
                },
                {
                    "ok": True,
                    "data": {
                        "confirmation_id": "00000000-0000-0000-0000-000000000001",
                        "videos": [],
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
            await run_agent("搜索并准备收藏动态视频")

        first_context = execute.await_args_list[0].args[2]
        second_context = execute.await_args_list[1].args[2]
        self.assertEqual(first_context.current_turn_id, second_context.current_turn_id)
        self.assertEqual(first_context.trusted_sources, [])
        self.assertEqual(
            [source.source_id for source in second_context.trusted_sources],
            ["bilibili:video:BV1trusted"],
        )


class SubtitleContextExtractionTest(unittest.TestCase):
    def test_extracts_subtitle_video_source_and_next_offset(self) -> None:
        result = {
            "ok": True,
            "data": {
                "status": "available",
                "bvid": "BV1subtitle",
                "cid": "987",
                "source_id": "bilibili:video:BV1subtitle",
                "track": {
                    "language": "zh-CN",
                    "display_name": "中文",
                    "source": "bilibili-human",
                },
                "cues": [
                    {
                        "index": 0,
                        "start_ms": 0,
                        "end_ms": 1000,
                        "text": "字幕证据",
                    }
                ],
                "total_cues": 2,
                "offset": 0,
                "limit": 1,
                "has_more": True,
                "next_offset": 1,
            },
        }

        sources = extract_sources(result, "get_video_subtitle")
        pagination = extract_pagination_state(
            "get_video_subtitle",
            {"bvid": "BV1subtitle", "cid": "987", "limit": 1},
            result,
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].bvid, "BV1subtitle")
        self.assertEqual(sources[0].cid, "987")
        self.assertEqual(sources[0].source_id, "bilibili:video:BV1subtitle")
        self.assertEqual(sources[0].source_tools, ["get_video_subtitle"])
        self.assertIsNotNone(pagination)
        assert pagination is not None
        self.assertEqual(
            pagination.next_arguments,
            {
                "bvid": "BV1subtitle",
                "cid": "987",
                "language": "zh-CN",
                "offset": 1,
                "limit": 1,
                "refresh": False,
            },
        )

    def test_unavailable_subtitle_is_not_a_citable_source(self) -> None:
        result = {
            "ok": True,
            "data": {
                "status": "unavailable",
                "bvid": "BV1subtitle",
                "cid": "987",
                "source_id": "bilibili:video:BV1subtitle",
                "has_more": False,
                "next_offset": None,
            },
        }

        self.assertEqual(extract_sources(result, "get_video_subtitle"), [])


if __name__ == "__main__":
    unittest.main()
