from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from bili_agent_cli.profile import PRIVACY_DIR

from .models import (
    MemoryCandidate,
    MemoryItem,
    MemorySourceType,
    MemoryState,
)

MEMORY_DB_PATH = PRIVACY_DIR / "memory.db"
SCHEMA_VERSION = 1


class MemoryStorageError(Exception):
    pass


class MemoryNotFoundError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self, path: Path = MEMORY_DB_PATH) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            connection = sqlite3.connect(self.path)
            self.path.chmod(0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise MemoryStorageError(str(error)) from error
        finally:
            if "connection" in locals():
                connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise MemoryStorageError("Memory 数据库版本高于当前程序")
            if version == 0:
                self._create_schema_v1(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _create_schema_v1(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE memory_items (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                topics_json TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                source_type TEXT NOT NULL,
                source_ref TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT
            );

            CREATE INDEX idx_memory_active_key 
            ON memory_items(state, key);

            CREATE INDEX idx_memory_updated
            ON memory_items(state, updated_at DESC);

            CREATE TABLE persona_snapshots (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                content_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE persona_refresh_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                counts_json TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
        """)

    def remember(
        self,
        candidate: MemoryCandidate,
        *,
        source_type: MemorySourceType,
        source_ref: str | None,
    ) -> MemoryItem:
        self.initialize()
        now = utc_now()

        with self.connect() as connection:
            current = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE key = ? AND kind = ? AND state = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (candidate.key, candidate.kind.value),
            ).fetchone()

            if (
                current is not None
                and _normalize(current["content"])
                == _normalize(candidate.content)
            ):
                confidence = max(
                    float(current["confidence"]),
                    candidate.confidence,
                )
                topics = sorted(
                    set(json.loads(current["topics_json"]))
                    | set(candidate.topics)
                )
                connection.execute(
                    """
                    UPDATE memory_items
                    SET topics_json = ?, confidence = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(topics, ensure_ascii=False),
                        confidence,
                        now.isoformat(),
                        current["id"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM memory_items WHERE id = ?",
                    (current["id"],),
                ).fetchone()
                return _row_to_memory(row)

            if current is not None:
                connection.execute(
                    """
                    UPDATE memory_items
                    SET state = 'superseded', updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), current["id"]),
                )

            item = MemoryItem(
                **candidate.model_dump(),
                source_type=source_type,
                source_ref=source_ref,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO memory_items (
                    id, key, kind, content, topics_json, confidence,
                    source_type, source_ref, state, created_at, updated_at,
                    last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.id),
                    item.key,
                    item.kind.value,
                    item.content,
                    json.dumps(item.topics, ensure_ascii=False),
                    item.confidence,
                    item.source_type.value,
                    item.source_ref,
                    item.state.value,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    None,
                ),
            )
            return item

    def list_memories(
        self,
        *,
        include_inactive: bool = False,
    ) -> list[MemoryItem]:
        self.initialize()
        query = "SELECT * FROM memory_items"
        parameters: tuple[str, ...] = ()
        if not include_inactive:
            query += " WHERE state = ?"
            parameters = (MemoryState.ACTIVE.value,)
        query += " ORDER BY updated_at DESC, id ASC"

        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_row_to_memory(row) for row in rows]

    def get_memory(self, memory_id: UUID) -> MemoryItem:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?",
                (str(memory_id),),
            ).fetchone()

        if row is None:
            raise MemoryNotFoundError(str(memory_id))
        return _row_to_memory(row)

    def update_memory(self, memory_id: UUID, content: str) -> MemoryItem:
        self.initialize()
        now = utc_now()
        normalized_content = content.strip()
        if not normalized_content or len(normalized_content) > 500:
            raise ValueError("Memory 内容长度必须在 1 到 500 之间")

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE id = ? AND state = 'active'
                """,
                (str(memory_id),),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(str(memory_id))

            connection.execute(
                """
                UPDATE memory_items
                SET content = ?, confidence = 1.0, source_type = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_content,
                    MemorySourceType.USER.value,
                    now.isoformat(),
                    str(memory_id),
                ),
            )
            updated_row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?",
                (str(memory_id),),
            ).fetchone()

        return _row_to_memory(updated_row)

    def soft_delete(self, memory_id: UUID) -> MemoryItem:
        self.initialize()
        now = utc_now()

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?",
                (str(memory_id),),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(str(memory_id))

            connection.execute(
                """
                UPDATE memory_items
                SET state = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    MemoryState.DELETED.value,
                    now.isoformat(),
                    str(memory_id),
                ),
            )
            deleted_row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?",
                (str(memory_id),),
            ).fetchone()

        return _row_to_memory(deleted_row)

    def mark_used(
        self,
        memory_ids: list[UUID],
        used_at: datetime,
    ) -> int:
        if not memory_ids:
            return 0

        self.initialize()
        placeholders = ",".join("?" for _ in memory_ids)
        parameters = (
            used_at.isoformat(),
            MemoryState.ACTIVE.value,
            *(str(memory_id) for memory_id in memory_ids),
        )

        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE memory_items
                SET last_used_at = ?
                WHERE state = ? AND id IN ({placeholders})
                """,
                parameters,
            )
            return cursor.rowcount


def _row_to_memory(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=UUID(row["id"]),
        key=row["key"],
        kind=row["kind"],
        content=row["content"],
        topics=json.loads(row["topics_json"]),
        confidence=row["confidence"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_used_at=(
            datetime.fromisoformat(row["last_used_at"])
            if row["last_used_at"] is not None
            else None
        ),
    )


def _normalize(text: str) -> str:
    return "".join(text.lower().split())
