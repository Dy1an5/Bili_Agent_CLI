from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .models import ConversationSession

class SessionNotFoundError(Exception):
    """请求继续一个不存在或已经过期的会话。"""

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
                id = uuid4(),
                created_at = now,
                updated_at = now,
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

    