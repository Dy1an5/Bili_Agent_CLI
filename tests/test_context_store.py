from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bili_agent_cli.agent.context import (
    AgentEvidenceBatch,
    ConversationTurn,
    FileSessionStore,
    InMemorySessionStore,
    SessionNotFoundError,
)
from bili_agent_cli.agent.context.models import PendingFavoriteSave
from bili_agent_cli.agent.context.store import CONVERSATIONS_DIR
from bili_agent_cli.profile import PROJECT_ROOT
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderPrivacy,
    FavoriteSavePlan,
    FavoriteSaveVideo,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class SessionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_get_return_same_session(self) -> None:
        store = InMemorySessionStore(ttl_seconds=60, capacity=10)

        created = await store.create()
        loaded = await store.get(created.id)

        self.assertIs(loaded, created)

    async def test_missing_and_expired_sessions_raise_not_found(self) -> None:
        clock = FakeClock()
        store = InMemorySessionStore(
            ttl_seconds=60,
            capacity=10,
            clock=clock,
        )
        session = await store.create()

        clock.advance(seconds=60)

        with self.assertRaises(SessionNotFoundError):
            await store.get(session.id)

    async def test_capacity_evicts_least_recently_used_session(self) -> None:
        clock = FakeClock()
        store = InMemorySessionStore(
            ttl_seconds=600,
            capacity=2,
            clock=clock,
        )
        first = await store.create()
        clock.advance(seconds=1)
        second = await store.create()
        clock.advance(seconds=1)
        await store.get(first.id)
        clock.advance(seconds=1)

        await store.create()

        self.assertIs(await store.get(first.id), first)
        with self.assertRaises(SessionNotFoundError):
            await store.get(second.id)

    async def test_locked_session_is_not_expired_or_evicted(self) -> None:
        clock = FakeClock()
        store = InMemorySessionStore(
            ttl_seconds=10,
            capacity=1,
            clock=clock,
        )
        session = await store.create()

        async with session.lock:
            clock.advance(seconds=10)
            other = await store.create()
            self.assertIs(await store.get(session.id), session)

        self.assertIs(await store.get(other.id), other)


class FileSessionStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name) / "conversations"

    def make_store(
        self,
        *,
        clock: FakeClock | None = None,
    ) -> FileSessionStore:
        return FileSessionStore(
            directory=self.directory,
            ttl_seconds=60,
            capacity=2,
            clock=clock or FakeClock(),
        )

    async def test_create_writes_json_and_markdown(self) -> None:
        store = self.make_store()
        session = await store.create()
        session.turns.append(
            ConversationTurn(
                user_content="推荐 F1 视频",
                assistant_content="这是回答",
                evidence_batches=[
                    AgentEvidenceBatch(
                        tool_call_id="call-1",
                        tool_name="search_videos",
                        arguments={"keyword": "F1"},
                        result={"ok": True, "data": {"videos": []}},
                    )
                ],
            )
        )
        await store.save(session)

        session_directory = self.directory / str(session.id)
        json_path = session_directory / "session.json"
        markdown_path = session_directory / "transcript.md"
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertEqual(json_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(markdown_path.stat().st_mode & 0o777, 0o600)
        markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("推荐 F1 视频", markdown)
        self.assertIn("search_videos", markdown)

    async def test_new_store_loads_saved_session(self) -> None:
        first_store = self.make_store()
        session = await first_store.create()
        session.summary = "持久化摘要"
        session.pending_turn = ConversationTurn(
            status="interrupted",
            user_content="继续翻页",
            error_code="ProviderTimeoutError",
        )
        await first_store.save(session)

        second_store = self.make_store()
        loaded = await second_store.get(session.id)

        self.assertEqual(loaded.summary, "持久化摘要")
        self.assertIsNotNone(loaded.pending_turn)
        self.assertEqual(loaded.pending_turn.user_content, "继续翻页")
        self.assertEqual(loaded.pending_turn.status, "interrupted")

    async def test_pending_favorite_confirmation_survives_restart(self) -> None:
        first_store = self.make_store()
        session = await first_store.create()
        now = datetime.now(timezone.utc)
        session.pending_favorite_save = PendingFavoriteSave(
            confirmation_id=session.id,
            prepared_turn_id=session.id,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            plan=FavoriteSavePlan(
                folder_title="UP 主动态",
                will_create_folder=True,
                privacy=FavoriteFolderPrivacy.PRIVATE,
                videos=[FavoriteSaveVideo(bvid="BV1trusted", aid="42")],
            ),
        )
        await first_store.save(session)

        loaded = await self.make_store().get(session.id)

        self.assertIsNotNone(loaded.pending_favorite_save)
        self.assertEqual(
            loaded.pending_favorite_save.confirmation_id,
            session.id,
        )
        self.assertEqual(
            loaded.pending_favorite_save.plan.videos[0].aid,
            "42",
        )

    async def test_cache_expiry_does_not_delete_saved_session(self) -> None:
        clock = FakeClock()
        store = self.make_store(clock=clock)
        session = await store.create()

        clock.advance(seconds=60)
        loaded = await store.get(session.id)

        self.assertEqual(loaded.id, session.id)

    async def test_lists_and_deletes_session_directories(self) -> None:
        store = self.make_store()
        first = await store.create()
        second = await store.create()

        self.assertEqual(
            set(await store.list_session_ids()),
            {first.id, second.id},
        )

        await store.delete(first.id)

        self.assertFalse((self.directory / str(first.id)).exists())
        with self.assertRaises(SessionNotFoundError):
            await store.get(first.id)


class ConversationDirectoryTest(unittest.TestCase):
    def test_default_directory_is_inside_privacy(self) -> None:
        self.assertEqual(
            CONVERSATIONS_DIR,
            PROJECT_ROOT / "privacy" / "conversations",
        )


if __name__ == "__main__":
    unittest.main()
