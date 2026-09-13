from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemorySourceType,
)
from bili_agent_cli.agent.memory.service import (
    render_memory_context,
    retrieve_memories,
    score_memory,
)
from bili_agent_cli.agent.memory.store import MemoryStore


class MemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = MemoryStore(
            Path(self.temporary_directory.name) / "memory.db"
        )

    def remember(
        self,
        *,
        key: str,
        kind: str,
        content: str,
        topics: list[str],
        confidence: float,
    ):
        return self.store.remember(
            MemoryCandidate(
                key=key,
                kind=kind,
                content=content,
                topics=topics,
                confidence=confidence,
            ),
            source_type=MemorySourceType.CONVERSATION,
            source_ref="session:test:turn:1",
        )

    def test_constraints_and_matching_topics_receive_higher_scores(self) -> None:
        preference = self.remember(
            key="interest.f1",
            kind="preference",
            content="喜欢 F1 技术分析",
            topics=["f1"],
            confidence=0.7,
        )
        constraint = self.remember(
            key="response.length",
            kind="constraint",
            content="回答保持简短",
            topics=[],
            confidence=1.0,
        )

        self.assertGreater(
            score_memory(constraint, "推荐赛车视频"),
            score_memory(preference, "推荐赛车视频"),
        )
        self.assertGreater(
            score_memory(preference, "推荐 F1 视频"),
            score_memory(preference, "推荐 Python 视频"),
        )

    def test_retrieve_respects_limit_and_marks_selected_items_used(self) -> None:
        constraint = self.remember(
            key="response.length",
            kind="constraint",
            content="回答保持简短",
            topics=[],
            confidence=1.0,
        )
        self.remember(
            key="interest.f1",
            kind="preference",
            content="喜欢 F1 技术分析",
            topics=["f1"],
            confidence=0.9,
        )

        selected = retrieve_memories(self.store, "随便问一个问题", limit=1)

        self.assertEqual([item.id for item in selected], [constraint.id])
        self.assertIsNotNone(self.store.get_memory(constraint.id).last_used_at)

    def test_render_memory_context_produces_bounded_data_shape(self) -> None:
        memory = self.remember(
            key="interest.f1",
            kind="preference",
            content="喜欢 F1 技术分析",
            topics=["f1"],
            confidence=0.9,
        )

        rendered = render_memory_context([memory])
        json_text = rendered.split("<long_term_memories>\n", 1)[1].split(
            "\n</long_term_memories>",
            1,
        )[0]
        payload = json.loads(json_text)

        self.assertEqual(payload[0]["id"], str(memory.id))
        self.assertEqual(payload[0]["kind"], "preference")
        self.assertNotIn("source_ref", payload[0])


if __name__ == "__main__":
    unittest.main()
