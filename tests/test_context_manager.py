from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from bili_agent_cli.agent.context import (
    AgentEvidenceBatch,
    ContextBudgetExceededError,
    ContextManager,
    ContextSettings,
    ConversationSession,
    ConversationTurn,
)
from bili_agent_cli.agent.context.manager import (
    estimate_payload_units,
    extract_pagination_state,
    extract_sources,
)
from bili_agent_cli.agent.models import AgentPaginationState, AgentSource


def make_session(*turns: ConversationTurn) -> ConversationSession:
    now = datetime.now(timezone.utc)
    return ConversationSession(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        turns=list(turns),
    )


def make_manager(
    *,
    max_input_units: int = 5_000,
    summarize_at_units: int = 4_000,
    recent_turns: int = 1,
    max_tool_result_units: int = 300,
) -> ContextManager:
    return ContextManager(
        ContextSettings(
            max_input_units=max_input_units,
            summarize_at_units=summarize_at_units,
            recent_turns=recent_turns,
            summary_max_tokens=123,
            max_tool_result_units=max_tool_result_units,
        )
    )


class ContextManagerTest(unittest.IsolatedAsyncioTestCase):
    def test_build_messages_includes_summary_turns_and_sources(self) -> None:
        source = AgentSource(bvid="BV1", title="视频一")
        session = make_session(
            ConversationTurn(
                user_content="第一问",
                assistant_content="第一答",
                sources=[source],
                evidence_batches=[
                    AgentEvidenceBatch(
                        tool_call_id="call-1",
                        tool_name="search_videos",
                        arguments={"keyword": "测试"},
                        result={
                            "ok": True,
                            "data": {
                                "videos": [
                                    {
                                        "source_id": "bilibili:video:BV1",
                                        "bvid": "BV1",
                                        "title": "视频一",
                                    }
                                ]
                            },
                        },
                    )
                ],
                pagination_states=[
                    AgentPaginationState(
                        tool="get_following_feed",
                        has_more=True,
                        next_arguments={"offset": "next-offset"},
                    )
                ],
            )
        )
        session.summary = "更早的摘要"

        messages = make_manager().build_messages(
            system_prompt="系统提示",
            session=session,
            task="当前问题",
        )

        self.assertEqual([message["role"] for message in messages], [
            "system",
            "user",
            "assistant",
            "user",
        ])
        self.assertIn("更早的摘要", messages[0]["content"])
        self.assertIn("<cited_sources>", messages[2]["content"])
        self.assertIn("<evidence_batches>", messages[2]["content"])
        self.assertIn("BV1", messages[2]["content"])
        self.assertIn("<pagination_state>", messages[2]["content"])
        self.assertIn("next-offset", messages[2]["content"])

    async def test_compaction_summarizes_only_old_turns(self) -> None:
        old_turn = ConversationTurn(
            user_content="旧问题" * 30,
            assistant_content="旧回答" * 30,
        )
        recent_turn = ConversationTurn(
            user_content="最近问题",
            assistant_content="最近回答",
        )
        session = make_session(old_turn, recent_turn)
        summarize = AsyncMock(return_value="压缩后的摘要")
        manager = make_manager(
            max_input_units=2_000,
            summarize_at_units=100,
        )

        compacted = await manager.compact_session_if_needed(
            system_prompt="系统",
            session=session,
            task="当前问题",
            tools=[],
            summarize=summarize,
        )

        self.assertTrue(compacted)
        self.assertEqual(session.summary, "压缩后的摘要")
        self.assertEqual(session.turns, [recent_turn])
        summary_input, max_tokens = summarize.await_args.args
        self.assertIn("旧问题", summary_input)
        self.assertNotIn("最近问题", summary_input)
        self.assertEqual(max_tokens, 123)

    async def test_summary_failure_uses_local_fallback(self) -> None:
        source = AgentSource(bvid="BVfallback", title="来源视频")
        old_turn = ConversationTurn(
            user_content="旧问题" * 20,
            assistant_content="旧回答" * 20,
            sources=[source],
        )
        session = make_session(
            old_turn,
            ConversationTurn(user_content="最近", assistant_content="保留"),
        )
        summarize = AsyncMock(side_effect=RuntimeError("summary failed"))
        manager = make_manager(
            max_input_units=2_000,
            summarize_at_units=100,
        )

        compacted = await manager.compact_session_if_needed(
            system_prompt="系统",
            session=session,
            task="当前",
            tools=[],
            summarize=summarize,
        )

        self.assertTrue(compacted)
        self.assertIn("旧问题", session.summary)
        self.assertIn("BVfallback", session.summary)

    def test_small_tool_result_is_unchanged(self) -> None:
        manager = make_manager()
        result = {"ok": True, "data": {"videos": [{"bvid": "BV1"}]}}

        self.assertIs(manager.compact_tool_result(result), result)

    def test_large_tool_result_is_trimmed_as_valid_json(self) -> None:
        manager = make_manager(max_tool_result_units=300)
        result = {
            "ok": True,
            "data": {
                "videos": [
                    {"bvid": f"BV{index}", "description": "内容" * 80}
                    for index in range(8)
                ]
            },
        }

        compacted = manager.compact_tool_result(result)

        json.dumps(compacted, ensure_ascii=False)
        truncation = compacted["context_truncation"]
        self.assertTrue(truncation["truncated"])
        self.assertEqual(truncation["fields"]["videos"]["original_count"], 8)
        self.assertLess(truncation["fields"]["videos"]["kept_count"], 8)
        self.assertLessEqual(estimate_payload_units(compacted), 300)

    def test_turn_evidence_budget_stops_additional_full_results(self) -> None:
        manager = ContextManager(
            ContextSettings(
                max_input_units=5_000,
                summarize_at_units=4_000,
                recent_turns=1,
                summary_max_tokens=123,
                max_tool_result_units=1_000,
                max_turn_evidence_units=100,
            )
        )
        existing = [
            AgentEvidenceBatch(
                tool_call_id="call-1",
                tool_name="search_videos",
                arguments={},
                result={"data": "x" * 200},
            )
        ]

        compacted = manager.compact_tool_result_for_turn(
            {"ok": True, "data": {"videos": [{"bvid": "BV1"}]}},
            existing,
        )

        self.assertEqual(
            compacted["context_truncation"]["reason"],
            "turn_evidence_budget_exhausted",
        )

    def test_ensure_fits_rejects_oversized_request(self) -> None:
        manager = make_manager(
            max_input_units=200,
            summarize_at_units=100,
        )
        messages = [{"role": "user", "content": "超长" * 200}]

        with self.assertRaises(ContextBudgetExceededError):
            manager.ensure_fits(messages, [])

    def test_extract_sources_supports_video_and_following_shapes(self) -> None:
        sources = extract_sources(
            {
                "ok": True,
                "data": {
                    "videos": [
                        {"bvid": "BV1", "cid": "1", "title": "视频一"},
                        {"bvid": "BV1", "cid": "1", "title": "重复"},
                    ],
                    "items": [
                        {
                            "author": {"name": "UP主"},
                            "video": {"bvid": "BV2", "title": "视频二"},
                        }
                    ],
                },
            }
        )

        self.assertEqual([source.bvid for source in sources], ["BV1", "BV2"])
        self.assertEqual(sources[1].author_name, "UP主")

    def test_extract_sources_supports_unified_compound_video_shape(self) -> None:
        sources = extract_sources(
            {
                "ok": True,
                "data": {
                    "videos": [
                        {
                            "identity": {"bvid": "BV1parted", "cid": "88"},
                            "author": {"mid": "1", "name": "UP主"},
                            "detail": {"title": "分P视频"},
                            "contexts": {
                                "favorite": {"folder_id": "10"}
                            },
                            "source_id": (
                                "bilibili:video:BV1parted:part:88"
                            ),
                        }
                    ]
                },
            }
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].cid, "88")
        self.assertEqual(sources[0].title, "分P视频")
        self.assertEqual(sources[0].folder_id, "10")
        self.assertEqual(
            sources[0].source_id,
            "bilibili:video:BV1parted:part:88",
        )

    def test_extracts_following_pagination_state(self) -> None:
        state = extract_pagination_state(
            "get_following_feed",
            {},
            {
                "ok": True,
                "data": {"has_more": True, "next_offset": "next-offset"},
            },
        )

        self.assertIsNotNone(state)
        self.assertEqual(state.next_arguments, {"offset": "next-offset"})

    def test_extracts_following_users_next_page_arguments(self) -> None:
        state = extract_pagination_state(
            "get_following_users",
            {"page": 2, "page_size": 20, "sort": "frequent"},
            {
                "ok": True,
                "data": {"has_more": True, "page": 2, "page_size": 20},
            },
        )

        self.assertIsNotNone(state)
        self.assertEqual(
            state.next_arguments,
            {"page": 3, "page_size": 20, "sort": "frequent"},
        )

    def test_extracts_user_dynamics_next_offset_with_user_mid(self) -> None:
        state = extract_pagination_state(
            "get_user_dynamics",
            {"user_mid": "456", "offset": ""},
            {
                "ok": True,
                "data": {
                    "user_mid": "456",
                    "has_more": True,
                    "next_offset": "opaque-cursor",
                },
            },
        )

        self.assertIsNotNone(state)
        self.assertEqual(
            state.next_arguments,
            {"offset": "opaque-cursor", "user_mid": "456"},
        )

    def test_extracts_next_page_arguments(self) -> None:
        state = extract_pagination_state(
            "search_videos",
            {"keyword": "F1", "page": 2, "page_size": 20},
            {
                "ok": True,
                "data": {"has_more": True, "page": 2, "page_size": 20},
            },
        )

        self.assertIsNotNone(state)
        self.assertEqual(
            state.next_arguments,
            {"keyword": "F1", "page": 3, "page_size": 20},
        )

    def test_extracts_history_cursor_arguments(self) -> None:
        state = extract_pagination_state(
            "get_watch_history",
            {"page_size": 10, "max": 1, "view_at": 2},
            {
                "ok": True,
                "data": {
                    "videos": [],
                    "page_size": 10,
                    "has_more": True,
                    "next_max": 123,
                    "next_view_at": 456,
                },
            },
        )

        self.assertIsNotNone(state)
        self.assertEqual(
            state.next_arguments,
            {"page_size": 10, "max": 123, "view_at": 456},
        )


if __name__ == "__main__":
    unittest.main()
