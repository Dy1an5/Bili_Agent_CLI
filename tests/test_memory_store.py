from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemoryDurability,
    MemoryObservationAction,
    MemoryScope,
    MemorySourceType,
    MemoryState,
)
from bili_agent_cli.agent.memory.store import MemoryNotFoundError, MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "data" / "memory.db"
        self.store = MemoryStore(self.database_path)

    @staticmethod
    def candidate(
        *,
        key: str = "recommendation.f1.analysis",
        content: str = "推荐 F1 时优先选择技术分析",
        confidence: float = 0.9,
        topics: list[str] | None = None,
        evidence_quote: str = "以后推荐 F1 时优先选择技术分析",
        durability: MemoryDurability = MemoryDurability.EXPLICIT,
        scope: MemoryScope = MemoryScope.TOPIC,
        scope_value: str | None = "f1",
    ) -> MemoryCandidate:
        return MemoryCandidate(
            key=key,
            kind="preference",
            content=content,
            topics=topics if topics is not None else ["f1"],
            confidence=confidence,
            evidence_quote=evidence_quote,
            durability=durability,
            scope=scope,
            scope_value=scope_value,
        )

    def observe(self, candidate: MemoryCandidate, source_ref: str = "turn:1"):
        return self.store.observe_many(
            [candidate],
            source_type=MemorySourceType.CONVERSATION,
            source_ref=source_ref,
        )[0]

    def test_initialize_creates_v2_private_database(self) -> None:
        self.store.initialize()
        self.store.initialize()

        self.assertTrue(self.database_path.is_file())
        self.assertEqual(self.database_path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.database_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            evidence_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='memory_evidence'"
            ).fetchone()
        self.assertEqual(version, 3)
        self.assertIsNotNone(evidence_table)

    def test_v1_migration_discards_untrusted_memories(self) -> None:
        self.database_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript("""
                CREATE TABLE memory_items (
                    id TEXT PRIMARY KEY, key TEXT, kind TEXT, content TEXT,
                    topics_json TEXT, confidence REAL, source_type TEXT,
                    source_ref TEXT, state TEXT, created_at TEXT,
                    updated_at TEXT, last_used_at TEXT
                );
                CREATE INDEX idx_memory_active_key ON memory_items(state, key);
                CREATE INDEX idx_memory_updated ON memory_items(state, updated_at);
                INSERT INTO memory_items VALUES (
                    'old', 'bad.memory', 'preference', '旧污染记忆', '[]', 0.9,
                    'conversation', 'turn:old', 'active', '2026-01-01',
                    '2026-01-01', NULL
                );
                PRAGMA user_version = 1;
            """)

        self.store.initialize()

        self.assertEqual(self.store.list_memories(include_inactive=True), [])
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_v2_migration_adds_persona_storage(self) -> None:
        self.database_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.database_path) as connection:
            self.store._create_schema_v2(connection)
            connection.execute("DROP TABLE persona_snapshots")
            connection.execute("DROP TABLE persona_refresh_runs")
            connection.execute("PRAGMA user_version = 2")

        self.store.initialize()

        with sqlite3.connect(self.database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertIn("persona_snapshots", tables)
        self.assertIn("persona_refresh_runs", tables)

    def test_explicit_candidate_is_active_with_evidence(self) -> None:
        result = self.observe(self.candidate())

        self.assertEqual(result.action, MemoryObservationAction.CREATED_ACTIVE)
        self.assertEqual(result.item.state, MemoryState.ACTIVE)
        self.assertEqual(result.item.evidence_count, 1)
        evidence = self.store.list_evidence(result.item.id)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].user_excerpt, result.item.content.replace("推荐", "以后推荐", 1))

    def test_inferred_candidate_requires_two_distinct_turns(self) -> None:
        candidate = self.candidate(
            key="recommendation.tech.creator_tier",
            content="视频推荐优先选择头部科技 UP 主",
            evidence_quote="筛选出头部科技up的视频",
            durability=MemoryDurability.INFERRED,
            scope=MemoryScope.INTENT,
            scope_value="video_recommendation",
            topics=["科技"],
        )
        first = self.observe(candidate, "turn:1")
        duplicate = self.observe(candidate, "turn:1")
        second = self.observe(candidate, "turn:2")

        self.assertEqual(first.item.state, MemoryState.PENDING)
        self.assertEqual(duplicate.item.evidence_count, 1)
        self.assertEqual(duplicate.action, MemoryObservationAction.UPDATED_PENDING)
        self.assertEqual(second.action, MemoryObservationAction.PROMOTED)
        self.assertEqual(second.item.state, MemoryState.ACTIVE)
        self.assertEqual(second.item.evidence_count, 2)

    def test_similar_content_with_different_key_is_merged(self) -> None:
        first = self.observe(self.candidate(), "turn:1")
        second = self.observe(
            self.candidate(
                key="preference.f1.analysis",
                content="推荐 F1 时优先选择技术分析内容",
                evidence_quote="我喜欢 F1 技术分析内容",
            ),
            "turn:2",
        )

        self.assertEqual(second.item.id, first.item.id)
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 1)

    def test_model_key_wording_drift_still_promotes_pending_memory(self) -> None:
        first = self.observe(
            self.candidate(
                key="recommendation.preferred_up_creators.head_tech",
                content=(
                    "在视频推荐场景中，倾向只保留头部 UP 主的视频，"
                    "过滤掉非头部 UP 主的内容。"
                ),
                evidence_quote="筛选出头部科技UP的视频",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
                topics=["科技"],
            ),
            "turn:1",
        )
        second = self.observe(
            self.candidate(
                key="recommendation.preferred_channel_tier",
                content="推荐视频时偏好只筛选头部UP主的内容。",
                evidence_quote="帮我筛选头部科技UP主的视频",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
                topics=["科技"],
            ),
            "turn:2",
        )

        self.assertEqual(second.item.id, first.item.id)
        self.assertEqual(second.action, MemoryObservationAction.PROMOTED)
        self.assertEqual(second.item.state, MemoryState.ACTIVE)
        self.assertEqual(second.item.evidence_count, 2)
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 1)

    def test_link_key_wording_drift_still_promotes_pending_memory(self) -> None:
        first = self.observe(
            self.candidate(
                key="response.format.include_url",
                content="偏好以网址（直接链接）的形式获取视频结果。",
                evidence_quote="给网址",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
                topics=[],
            ),
            "turn:1",
        )
        second = self.observe(
            self.candidate(
                key="recommendation.output_format",
                content="推荐视频时希望直接给出链接。",
                evidence_quote="直接给链接",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
                topics=[],
            ),
            "turn:2",
        )

        self.assertEqual(second.item.id, first.item.id)
        self.assertEqual(second.action, MemoryObservationAction.PROMOTED)
        self.assertEqual(second.item.state, MemoryState.ACTIVE)
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 1)

    def test_conflicting_same_slot_supersedes_previous(self) -> None:
        previous = self.observe(self.candidate(), "turn:1").item
        current = self.observe(
            self.candidate(
                content="推荐 F1 时不要选择技术分析",
                evidence_quote="以后推荐 F1 时不要选择技术分析",
            ),
            "turn:2",
        ).item

        self.assertNotEqual(current.id, previous.id)
        by_id = {
            item.id: item for item in self.store.list_memories(include_inactive=True)
        }
        self.assertEqual(by_id[previous.id].state, MemoryState.SUPERSEDED)
        self.assertEqual(by_id[current.id].state, MemoryState.ACTIVE)

    def test_negation_supersedes_even_when_text_is_highly_similar(self) -> None:
        previous = self.observe(
            self.candidate(content="推荐 F1 时优先选择官方账号"), "turn:1"
        ).item
        current = self.observe(
            self.candidate(
                content="推荐 F1 时不要优先选择官方账号",
                evidence_quote="以后推荐 F1 时不要优先选择官方账号",
            ),
            "turn:2",
        ).item

        self.assertNotEqual(current.id, previous.id)
        self.assertEqual(self.store.get_memory(previous.id).state, MemoryState.SUPERSEDED)
        self.assertEqual(current.state, MemoryState.ACTIVE)

    def test_pending_expires_after_thirty_days(self) -> None:
        pending = self.observe(
            self.candidate(durability=MemoryDurability.INFERRED), "turn:1"
        ).item
        old = datetime.now(timezone.utc) - timedelta(days=31)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE memory_items SET last_observed_at = ? WHERE id = ?",
                (old.isoformat(), str(pending.id)),
            )

        self.store.expire_pending()

        self.assertEqual(self.store.get_memory(pending.id).state, MemoryState.EXPIRED)

    def test_manual_confirm_edit_delete_and_restore(self) -> None:
        pending = self.observe(
            self.candidate(durability=MemoryDurability.INFERRED), "turn:1"
        ).item
        confirmed = self.store.confirm(pending.id)
        edited = self.store.update_memory(confirmed.id, "推荐 F1 时优先官方内容")
        deleted = self.store.soft_delete(edited.id)
        restored = self.store.restore(deleted.id)

        self.assertEqual(confirmed.state, MemoryState.ACTIVE)
        self.assertEqual(edited.content, "推荐 F1 时优先官方内容")
        self.assertEqual(edited.source_type, MemorySourceType.USER)
        self.assertEqual(deleted.state, MemoryState.DELETED)
        self.assertEqual(restored.state, MemoryState.ACTIVE)
        self.assertEqual(restored.confidence, 1.0)

    def test_missing_memory_operations_raise_not_found(self) -> None:
        missing = uuid4()
        for operation in (
            lambda: self.store.get_memory(missing),
            lambda: self.store.confirm(missing),
            lambda: self.store.update_memory(missing, "新内容"),
            lambda: self.store.soft_delete(missing),
            lambda: self.store.restore(missing),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(MemoryNotFoundError):
                    operation()

    def test_mark_injected_updates_only_active_memories(self) -> None:
        active = self.observe(self.candidate(), "turn:1").item
        pending = self.observe(
            self.candidate(
                key="recommendation.tech.creator_tier",
                content="视频推荐优先选择头部科技 UP 主",
                evidence_quote="筛选头部科技 UP 主",
                durability=MemoryDurability.INFERRED,
                scope=MemoryScope.INTENT,
                scope_value="video_recommendation",
            ),
            "turn:2",
        ).item
        injected_at = datetime(2026, 9, 13, 1, 2, 3, tzinfo=timezone.utc)

        count = self.store.mark_injected([active.id, pending.id], injected_at)

        self.assertEqual(count, 1)
        self.assertEqual(self.store.get_memory(active.id).last_injected_at, injected_at)
        self.assertIsNone(self.store.get_memory(pending.id).last_injected_at)


if __name__ == "__main__":
    unittest.main()
