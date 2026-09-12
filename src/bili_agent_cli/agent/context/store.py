from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from bili_agent_cli.profile import PRIVACY_DIR

from .models import ConversationSession, ConversationSessionRecord
from .transcript import render_session_markdown


CONVERSATIONS_DIR = PRIVACY_DIR / "conversations"


class SessionNotFoundError(Exception):
    """请求继续一个不存在或已经过期的会话。"""


class SessionStorageError(Exception):
    """会话文件无法读取或写入。"""


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemorySessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        capacity: int,
        clock: Clock = utc_now,
    ) -> None:
        """
        ttl_seconds: 会话多久不用过期
        capacity: 最多保留多少会话
        clock: 获取当前时间方法
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于0")
        if capacity <= 0:
            raise ValueError("capacity 必须大于0")

        self._ttl = timedelta(seconds=ttl_seconds)
        self._capacity = capacity
        self._clock = clock
        self._sessions: dict[UUID, ConversationSession] = {}
        self._store_lock = asyncio.Lock()

    async def create(self) -> ConversationSession:
        async with self._store_lock:
            now = self._clock()
            self._remove_expired(now)
            self._evict_if_full()

            session = ConversationSession(
                id=uuid4(),
                created_at=now,
                updated_at=now,
            )
            self._sessions[session.id] = session
            return session

    async def get(self, session_id: UUID) -> ConversationSession:
        async with self._store_lock:
            now = self._clock()
            self._remove_expired(now)
            session = self._sessions.get(session_id)

            if session is None:
                raise SessionNotFoundError(str(session_id))

            session.updated_at = now
            return session

    async def delete(self, session_id: UUID) -> None:
        async with self._store_lock:
            if self._sessions.pop(session_id, None) is None:
                raise SessionNotFoundError(str(session_id))

    def _remove_expired(self, now: datetime) -> None:
        expired_ids = []

        for session_id, session in self._sessions.items():
            if now - session.updated_at >= self._ttl and not session.lock.locked():
                expired_ids.append(session_id)

        for session_id in expired_ids:
            del self._sessions[session_id]

    def _evict_if_full(self) -> None:
        if len(self._sessions) < self._capacity:
            return

        candidates = []

        for session in self._sessions.values():
            if not session.lock.locked():
                candidates.append(session)

        if not candidates:
            return

        oldest = min(candidates, key=lambda session: session.updated_at)
        del self._sessions[oldest.id]


class FileSessionStore:
    """以 JSON 为真实数据源，并同步生成人类可读 Markdown。"""

    def __init__(
        self,
        *,
        directory: Path = CONVERSATIONS_DIR,
        ttl_seconds: int,
        capacity: int,
        clock: Clock = utc_now,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于0")
        if capacity <= 0:
            raise ValueError("capacity 必须大于0")

        self.directory = directory
        self._cache_ttl = timedelta(seconds=ttl_seconds)
        self._capacity = capacity
        self._clock = clock
        self._sessions: dict[UUID, ConversationSession] = {}
        self._accessed_at: dict[UUID, datetime] = {}
        self._store_lock = asyncio.Lock()

    async def create(self) -> ConversationSession:
        async with self._store_lock:
            now = self._clock()
            self._remove_expired_cache(now)
            self._evict_cache_if_full()
            session = ConversationSession(
                id=uuid4(),
                created_at=now,
                updated_at=now,
            )
            self._write_session(session)
            self._sessions[session.id] = session
            self._accessed_at[session.id] = now
            return session

    async def get(self, session_id: UUID) -> ConversationSession:
        async with self._store_lock:
            now = self._clock()
            self._remove_expired_cache(now)
            session = self._sessions.get(session_id)
            if session is None:
                self._evict_cache_if_full()
                session = self._read_session(session_id)
                self._sessions[session.id] = session
            self._accessed_at[session.id] = now
            return session

    async def save(self, session: ConversationSession) -> None:
        async with self._store_lock:
            self._write_session(session)
            self._sessions[session.id] = session
            self._accessed_at[session.id] = self._clock()

    async def delete(self, session_id: UUID) -> None:
        session = await self.get(session_id)
        async with session.lock:
            async with self._store_lock:
                session_directory = self._session_directory(session_id)
                if not (session_directory / "session.json").is_file():
                    raise SessionNotFoundError(str(session_id))
                try:
                    shutil.rmtree(session_directory)
                except OSError as exc:
                    raise SessionStorageError(
                        f"无法删除会话 {session_id}"
                    ) from exc
                self._sessions.pop(session_id, None)
                self._accessed_at.pop(session_id, None)

    async def list_session_ids(self) -> list[UUID]:
        async with self._store_lock:
            return self._list_session_ids()

    def _ensure_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    def _session_directory(self, session_id: UUID) -> Path:
        return self.directory / str(session_id)

    def _read_session(self, session_id: UUID) -> ConversationSession:
        session_directory = self._session_directory(session_id)
        session_path = session_directory / "session.json"
        if session_directory.is_symlink() or not session_path.is_file():
            raise SessionNotFoundError(str(session_id))

        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
            record = ConversationSessionRecord.model_validate(payload)
        except (OSError, ValueError, ValidationError) as exc:
            raise SessionStorageError(
                f"无法读取会话 {session_id}"
            ) from exc

        if record.id != session_id:
            raise SessionStorageError(f"会话目录与记录 ID 不一致: {session_id}")
        return ConversationSession.from_record(record)

    def _write_session(self, session: ConversationSession) -> None:
        try:
            self._ensure_directory()
            session_directory = self._session_directory(session.id)
            if session_directory.is_symlink():
                raise SessionStorageError(
                    f"会话目录不能是符号链接: {session.id}"
                )
            session_directory.mkdir(mode=0o700, exist_ok=True)
            session_directory.chmod(0o700)

            record = session.to_record()
            json_content = record.model_dump_json(indent=2) + "\n"
            markdown_content = render_session_markdown(session)
            self._write_atomic(
                session_directory / "session.json",
                json_content,
            )
            self._write_atomic(
                session_directory / "transcript.md",
                markdown_content,
            )
        except SessionStorageError:
            raise
        except (OSError, ValueError, ValidationError) as exc:
            raise SessionStorageError(
                f"无法写入会话 {session.id}"
            ) from exc

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            temporary_path.chmod(0o600)
            temporary_path.replace(path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _list_session_ids(self) -> list[UUID]:
        if not self.directory.is_dir():
            return []

        session_ids: list[UUID] = []
        for entry in self.directory.iterdir():
            if entry.is_symlink() or not (entry / "session.json").is_file():
                continue
            try:
                session_ids.append(UUID(entry.name))
            except ValueError:
                continue
        return sorted(session_ids, key=str)

    def _remove_expired_cache(self, now: datetime) -> None:
        expired_ids = [
            session_id
            for session_id, accessed_at in self._accessed_at.items()
            if now - accessed_at >= self._cache_ttl
            and not self._sessions[session_id].lock.locked()
        ]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)
            self._accessed_at.pop(session_id, None)

    def _evict_cache_if_full(self) -> None:
        if len(self._sessions) < self._capacity:
            return

        candidates = [
            session_id
            for session_id, session in self._sessions.items()
            if not session.lock.locked()
        ]
        if not candidates:
            return

        oldest_id = min(candidates, key=self._accessed_at.__getitem__)
        self._sessions.pop(oldest_id, None)
        self._accessed_at.pop(oldest_id, None)
