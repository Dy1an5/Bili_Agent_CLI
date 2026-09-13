from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemoryDurability,
    MemoryScope,
    MemorySourceType,
)
from bili_agent_cli.agent.memory.service import (
    filter_memory_candidates,
    infer_task_intents,
    process_memory_candidates,
    render_memory_context,
    retrieve_memories,
    score_memory,
    should_extract_memory,
)
from bili_agent_cli.agent.memory.store import MemoryStore


class MemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = MemoryStore(Path(self.temporary_directory.name) / "memory.db")

    @staticmethod
    def candidate(
        *,
        key: str,
        content: str,
        topics: list[str],
        scope: MemoryScope,
        scope_value: str | None,
        kind: str = "preference",
        evidence_quote: str | None = None,
        durability: MemoryDurability = MemoryDurability.EXPLICIT,
        confidence: float = 0.9,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            key=key,
            kind=kind,
            content=content,
            topics=topics,
            confidence=confidence,
            evidence_quote=evidence_quote or content,
            durability=durability,
            scope=scope,
            scope_value=scope_value,
        )

    def remember(self, candidate: MemoryCandidate, source_ref: str):
        return self.store.remember(
            candidate,
            source_type=MemorySourceType.CONVERSATION,
            source_ref=source_ref,
        )

    def test_prefilter_only_calls_extractor_for_memory_like_messages(self) -> None:
        self.assertTrue(should_extract_memory("以后推荐 F1 时优先官方账号"))
        self.assertTrue(should_extract_memory("筛选头部科技 UP 的视频，给网址"))
        self.assertFalse(should_extract_memory("apple"))
        self.assertFalse(should_extract_memory("最近的 F1 杆位是谁"))
        self.assertFalse(should_extract_memory("记住 API Key sk-1234567890abcdef"))

    def test_candidate_requires_user_evidence_and_downgrades_one_off_task(self) -> None:
        candidate = self.candidate(
            key="recommendation.video.links",
            content="视频结果直接给链接",
            topics=[],
            scope=MemoryScope.INTENT,
            scope_value="video_recommendation",
            evidence_quote="直接给链接",
        )

        accepted = filter_memory_candidates(
            [candidate], user_content="筛选头部科技 UP 的视频，直接给链接"
        )
        rejected = filter_memory_candidates(
            [candidate], user_content="完全没有这段证据"
        )

        self.assertEqual(accepted[0].durability, MemoryDurability.INFERRED)
        self.assertEqual(rejected, [])

    def test_explicit_marker_only_applies_to_its_own_candidate(self) -> None:
        long_term = self.candidate(
            key="response.length",
            kind="constraint",
            content="回答保持简短",
            topics=[],
            scope=MemoryScope.GLOBAL,
            scope_value=None,
            evidence_quote="以后回答保持简短",
            durability=MemoryDurability.INFERRED,
        )
        one_off = self.candidate(
            key="recommendation.video.links",
            content="视频结果直接给链接",
            topics=[],
            scope=MemoryScope.INTENT,
            scope_value="video_recommendation",
            evidence_quote="这次直接给链接",
        )

        accepted = filter_memory_candidates(
            [long_term, one_off],
            user_content="以后回答保持简短，这次直接给链接",
        )

        self.assertEqual(accepted[0].durability, MemoryDurability.EXPLICIT)
        self.assertEqual(accepted[1].durability, MemoryDurability.INFERRED)

    def test_explicit_statement_is_activated_and_one_off_is_pending(self) -> None:
        explicit = self.candidate(
            key="recommendation.f1.creators",
            content="推荐 F1 时优先指定 UP 主",
            topics=["f1"],
            scope=MemoryScope.TOPIC,
            scope_value="f1",
            evidence_quote="以后推荐F1时优先指定UP主",
            durability=MemoryDurability.INFERRED,
        )
        explicit_result = process_memory_candidates(
            self.store,
            [explicit],
            user_content="以后推荐F1时优先指定UP主",
            source_ref="turn:1",
        )

        inferred = self.candidate(
            key="recommendation.tech.creator_tier",
            content="视频推荐优先头部科技 UP 主",
            topics=["科技"],
            scope=MemoryScope.INTENT,
            scope_value="video_recommendation",
            evidence_quote="筛选头部科技UP的视频",
        )
        inferred_result = process_memory_candidates(
            self.store,
            [inferred],
            user_content="筛选头部科技UP的视频",
            source_ref="turn:2",
        )

        self.assertEqual(explicit_result.active_saved_count, 1)
        self.assertEqual(inferred_result.pending_saved_count, 1)

    def test_local_retrieval_respects_scope(self) -> None:
        f1 = self.remember(
            self.candidate(
                key="recommendation.f1.creators",
                content="推荐 F1 时优先 F1赛事资讯",
                topics=["f1"],
                scope=MemoryScope.TOPIC,
                scope_value="f1",
            ),
            "turn:f1",
        )
        tech = self.remember(
            self.candidate(
                key="recommendation.video.creator_tier",
                content="视频推荐优先头部科技 UP 主",
                topics=["科技"],
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
            ),
            "turn:tech",
        )

        apple = retrieve_memories(self.store, "apple")
        f1_results = retrieve_memories(self.store, "推荐 F1 资讯")

        self.assertIn(tech.id, [item.id for item in apple])
        self.assertNotIn(f1.id, [item.id for item in apple])
        self.assertIn(f1.id, [item.id for item in f1_results])

    def test_global_constraint_is_retrieved_but_pending_is_not(self) -> None:
        global_constraint = self.remember(
            self.candidate(
                key="response.length",
                kind="constraint",
                content="回答保持简短",
                topics=[],
                scope=MemoryScope.GLOBAL,
                scope_value=None,
            ),
            "turn:global",
        )
        pending = self.remember(
            self.candidate(
                key="recommendation.video.links",
                content="视频结果直接给链接",
                topics=[],
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
                durability=MemoryDurability.INFERRED,
            ),
            "turn:pending",
        )

        selected = retrieve_memories(self.store, "随便问一个问题")

        self.assertIn(global_constraint.id, [item.id for item in selected])
        self.assertNotIn(pending.id, [item.id for item in selected])
        self.assertIsNotNone(
            self.store.get_memory(global_constraint.id).last_injected_at
        )

    def test_render_memory_context_omits_source_evidence(self) -> None:
        memory = self.remember(
            self.candidate(
                key="recommendation.f1.creators",
                content="推荐 F1 时优先官方账号",
                topics=["f1"],
                scope=MemoryScope.TOPIC,
                scope_value="f1",
            ),
            "turn:f1",
        )

        rendered = render_memory_context([memory])
        json_text = rendered.split("<long_term_memories>\n", 1)[1].split(
            "\n</long_term_memories>", 1
        )[0]
        payload = json.loads(json_text)

        self.assertEqual(payload[0]["scope"], "topic")
        self.assertEqual(payload[0]["scope_value"], "f1")
        self.assertNotIn("source_ref", payload[0])

    def test_scoring_rejects_irrelevant_high_confidence_memory(self) -> None:
        item = self.remember(
            self.candidate(
                key="recommendation.f1.creators",
                content="推荐 F1 时优先官方账号",
                topics=["f1"],
                scope=MemoryScope.TOPIC,
                scope_value="f1",
                confidence=1.0,
            ),
            "turn:f1",
        )

        self.assertGreater(score_memory(item, "推荐 F1 资讯"), 0)
        self.assertEqual(score_memory(item, "apple"), 0)
        self.assertIn("video_recommendation", infer_task_intents("apple"))


if __name__ == "__main__":
    unittest.main()
