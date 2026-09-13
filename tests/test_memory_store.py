from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemorySourceType,
    MemoryState,
)
from bili_agent_cli.agent.memory.store import (
    MemoryNotFoundError,
    MemoryStore,
)


class MemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "data" / "memory.db"
        )
        self.store = MemoryStore(self.database_path)

    @staticmethod
    def candidate(
        *,
        content: str = "喜欢 F1 技术分析",
        confidence: float = 0.7,
        topics: list[str] | None = None,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            key="interest.f1",
            kind="preference",
            content=content,
            topics=topics if topics is not None else ["f1"],
            confidence=confidence,
        )

    def remember(self, candidate: MemoryCandidate | None = None):
        return self.store.remember(
            candidate or self.candidate(),
            source_type=MemorySourceType.CONVERSATION,
            source_ref="session:test:turn:1",
        )

    def test_initialize_creates_versioned_private_database(self) -> None:
        self.store.initialize()
        self.store.initialize()

        self.assertTrue(self.database_path.is_file())
        self.assertEqual(self.database_path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.database_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 1)

    def test_remember_lists_and_gets_memory(self) -> None:
        remembered = self.remember()

        self.assertEqual(self.store.get_memory(remembered.id), remembered)
        self.assertEqual(self.store.list_memories(), [remembered])
        self.assertEqual(remembered.source_ref, "session:test:turn:1")
        self.assertEqual(remembered.state, MemoryState.ACTIVE)

    def test_equivalent_memory_is_merged(self) -> None:
        first = self.remember(
            self.candidate(confidence=0.4, topics=["f1"]),
        )
        second = self.remember(
            self.candidate(
                content=" 喜欢  F1技术分析 ",
                confidence=0.9,
                topics=["赛事"],
            ),
        )

        self.assertEqual(second.id, first.id)
        self.assertEqual(second.confidence, 0.9)
        self.assertEqual(second.topics, ["f1", "赛事"])
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 1)

    def test_conflicting_memory_supersedes_previous_item(self) -> None:
        previous = self.remember()
        current = self.remember(
            self.candidate(content="不再关注 F1 技术分析"),
        )

        self.assertNotEqual(current.id, previous.id)
        self.assertEqual(self.store.list_memories(), [current])
        all_items = self.store.list_memories(include_inactive=True)
        by_id = {item.id: item for item in all_items}
        self.assertEqual(by_id[previous.id].state, MemoryState.SUPERSEDED)
        self.assertEqual(by_id[current.id].state, MemoryState.ACTIVE)

    def test_update_marks_memory_as_explicit_user_value(self) -> None:
        remembered = self.remember()

        updated = self.store.update_memory(
            remembered.id,
            "  更喜欢 F1 车队策略分析  ",
        )

        self.assertEqual(updated.content, "更喜欢 F1 车队策略分析")
        self.assertEqual(updated.confidence, 1.0)
        self.assertEqual(updated.source_type, MemorySourceType.USER)
        self.assertGreaterEqual(updated.updated_at, remembered.updated_at)

        for invalid_content in ("", "   ", "x" * 501):
            with self.subTest(invalid_content=invalid_content[:10]):
                with self.assertRaises(ValueError):
                    self.store.update_memory(remembered.id, invalid_content)

    def test_soft_delete_hides_memory_from_active_list(self) -> None:
        remembered = self.remember()

        deleted = self.store.soft_delete(remembered.id)

        self.assertEqual(deleted.state, MemoryState.DELETED)
        self.assertEqual(self.store.list_memories(), [])
        self.assertEqual(
            self.store.get_memory(remembered.id).state,
            MemoryState.DELETED,
        )
        with self.assertRaises(MemoryNotFoundError):
            self.store.update_memory(remembered.id, "不能修改已删除记录")

    def test_missing_memory_operations_raise_not_found(self) -> None:
        missing_id = uuid4()

        with self.assertRaises(MemoryNotFoundError):
            self.store.get_memory(missing_id)
        with self.assertRaises(MemoryNotFoundError):
            self.store.update_memory(missing_id, "新内容")
        with self.assertRaises(MemoryNotFoundError):
            self.store.soft_delete(missing_id)

    def test_mark_used_updates_only_active_memories(self) -> None:
        active = self.remember()
        deleted = self.store.remember(
            MemoryCandidate(
                key="response.length",
                kind="constraint",
                content="回答保持简短",
                confidence=1.0,
            ),
            source_type=MemorySourceType.USER,
            source_ref=None,
        )
        self.store.soft_delete(deleted.id)
        used_at = datetime(2026, 9, 13, 1, 2, 3, tzinfo=timezone.utc)

        updated_count = self.store.mark_used(
            [active.id, deleted.id],
            used_at,
        )

        self.assertEqual(updated_count, 1)
        self.assertEqual(self.store.get_memory(active.id).last_used_at, used_at)
        self.assertIsNone(self.store.get_memory(deleted.id).last_used_at)
        self.assertEqual(self.store.mark_used([], used_at), 0)


if __name__ == "__main__":
    unittest.main()
