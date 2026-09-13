from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID, uuid4

from bili_agent_cli.profile import PRIVACY_DIR

from .models import (
    MemoryCandidate,
    MemoryDurability,
    MemoryEvidence,
    MemoryItem,
    MemoryObservationAction,
    MemoryObservationResult,
    MemorySourceType,
    MemoryState,
)

MEMORY_DB_PATH = PRIVACY_DIR / "memory.db"
SCHEMA_VERSION = 2
PENDING_TTL_DAYS = 30
SIMILARITY_THRESHOLD = 0.65
KEY_SIGNATURE_STOP_WORDS = {
    "constraint",
    "direct",
    "directly",
    "format",
    "only",
    "output",
    "prefer",
    "preference",
    "preferred",
    "provide",
    "recommendation",
    "response",
}
KEY_SIGNATURE_ALIASES = {
    "creators": "creator",
    "head": "top",
    "uploader": "creator",
    "uploaders": "creator",
    "up": "creator",
    "links": "link",
    "uri": "link",
    "uris": "link",
    "url": "link",
    "urls": "link",
    "website": "link",
}
NEGATION_PATTERN = re.compile(
    r"不(?:要|再|喜欢|看|需要|希望|接受|推荐|使用|关注|提供|输出)|"
    r"别|避免|禁止|取消|停止"
)


class MemoryStorageError(Exception):
    pass


class MemoryNotFoundError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def text_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    ascii_tokens = set(re.findall(r"[a-z0-9]+", normalized))
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    cjk_tokens: set[str] = set()
    for run in cjk_runs:
        if len(run) == 1:
            cjk_tokens.add(run)
        else:
            cjk_tokens.update(
                run[index : index + 2] for index in range(len(run) - 1)
            )
    return ascii_tokens | cjk_tokens


def text_similarity(left: str, right: str) -> float:
    left_tokens = text_tokens(left)
    right_tokens = text_tokens(right)
    if not left_tokens or not right_tokens:
        return float(normalize_text(left) == normalize_text(right))
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def memory_key_signature(key: str) -> frozenset[str]:
    """Return stable semantic key parts while ignoring model wording drift."""
    parts = re.findall(r"[a-z0-9]+", key.lower())
    return frozenset(
        KEY_SIGNATURE_ALIASES.get(part, part)
        for part in parts
        if part not in KEY_SIGNATURE_STOP_WORDS
    )


def memory_key_signatures_match(left: str, right: str) -> bool:
    left_signature = memory_key_signature(left)
    right_signature = memory_key_signature(right)
    if not left_signature or not right_signature:
        return False
    if left_signature == right_signature:
        return True
    shared = left_signature & right_signature
    smaller_size = min(len(left_signature), len(right_signature))
    return smaller_size >= 2 and len(shared) == smaller_size


def normalize_memory_evidence(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    replacements = (
        (r"(?:网址|链接|\burls?\b|\buris?\b|\blinks?\b|\bwebsite\b)", "链接"),
        (r"(?:up\s*主?|创作者|上传者|\bcreators?\b|\buploaders?\b|\bchannels?\b)", "创作者"),
        (r"(?:头部|顶级|知名|\btop\b|\bhead\b)", "头部"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"帮我|麻烦|请|这次|一下|直接|给出|提供|返回|附上|给|"
        r"筛选出|筛选|只推荐|推荐|希望|我想要",
        "",
        normalized,
    )
    return normalize_text(normalized)


def memory_evidence_similarity(left: str, right: str) -> float:
    normalized_left = normalize_memory_evidence(left)
    normalized_right = normalize_memory_evidence(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    if min(len(normalized_left), len(normalized_right)) < 6:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def has_conflicting_polarity(left: str, right: str) -> bool:
    left_negative = NEGATION_PATTERN.search(left) is not None
    right_negative = NEGATION_PATTERN.search(right) is not None
    return left_negative != right_negative


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
                self._create_schema_v2(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version == 1:
                self._migrate_v1_to_v2(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _create_schema_v2(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE memory_items (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                topics_json TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                durability TEXT NOT NULL,
                scope TEXT NOT NULL,
                scope_value TEXT,
                source_type TEXT NOT NULL,
                source_ref TEXT,
                state TEXT NOT NULL,
                evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                activated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_injected_at TEXT
            );

            CREATE UNIQUE INDEX idx_memory_live_key
            ON memory_items(key, kind)
            WHERE state IN ('pending', 'active');

            CREATE INDEX idx_memory_scope
            ON memory_items(state, scope, scope_value);

            CREATE INDEX idx_memory_updated
            ON memory_items(state, updated_at DESC);

            CREATE TABLE memory_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                source_ref TEXT NOT NULL,
                user_excerpt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(memory_id, source_ref)
            );

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

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        # v1 自动提取记录没有可靠证据和作用域。按产品决策直接清空。
        connection.executescript("""
            DROP TABLE memory_items;
            DROP INDEX IF EXISTS idx_memory_active_key;
            DROP INDEX IF EXISTS idx_memory_updated;

            CREATE TABLE memory_items (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                topics_json TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                durability TEXT NOT NULL,
                scope TEXT NOT NULL,
                scope_value TEXT,
                source_type TEXT NOT NULL,
                source_ref TEXT,
                state TEXT NOT NULL,
                evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                activated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_injected_at TEXT
            );

            CREATE UNIQUE INDEX idx_memory_live_key
            ON memory_items(key, kind)
            WHERE state IN ('pending', 'active');

            CREATE INDEX idx_memory_scope
            ON memory_items(state, scope, scope_value);

            CREATE INDEX idx_memory_updated
            ON memory_items(state, updated_at DESC);

            CREATE TABLE memory_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                source_ref TEXT NOT NULL,
                user_excerpt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(memory_id, source_ref)
            );
        """)

    def observe_many(
        self,
        candidates: Sequence[MemoryCandidate],
        *,
        source_type: MemorySourceType,
        source_ref: str,
    ) -> list[MemoryObservationResult]:
        self.initialize()
        now = utc_now()
        with self.connect() as connection:
            self._expire_pending(connection, now)
            return [
                self._observe(
                    connection,
                    candidate,
                    source_type=source_type,
                    source_ref=source_ref,
                    now=now,
                )
                for candidate in candidates
            ]

    def remember(
        self,
        candidate: MemoryCandidate,
        *,
        source_type: MemorySourceType,
        source_ref: str | None,
    ) -> MemoryItem:
        reference = source_ref or f"manual:{utc_now().isoformat()}"
        return self.observe_many(
            [candidate],
            source_type=source_type,
            source_ref=reference,
        )[0].item

    def _observe(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        source_type: MemorySourceType,
        source_ref: str,
        now: datetime,
    ) -> MemoryObservationResult:
        live_rows = connection.execute(
            "SELECT * FROM memory_items WHERE state IN ('pending', 'active')"
        ).fetchall()
        exact = next(
            (
                row
                for row in live_rows
                if row["key"] == candidate.key
                and row["kind"] == candidate.kind.value
            ),
            None,
        )
        evidence_by_memory = self._load_evidence(connection, live_rows)
        exact_similarity = (
            self._candidate_match_score(exact, candidate, evidence_by_memory)
            if exact is not None
            else 0.0
        )

        if (
            exact is not None
            and (
                has_conflicting_polarity(exact["content"], candidate.content)
                or exact_similarity < SIMILARITY_THRESHOLD
            )
        ):
            connection.execute(
                "UPDATE memory_items SET state = ?, updated_at = ? WHERE id = ?",
                (MemoryState.SUPERSEDED.value, now.isoformat(), exact["id"]),
            )
            created = self._insert_item(
                connection,
                candidate,
                source_type=source_type,
                source_ref=source_ref,
                now=now,
            )
            return MemoryObservationResult(
                item=created,
                action=MemoryObservationAction.SUPERSEDED,
            )

        match = exact or self._find_semantic_match(
            live_rows,
            candidate,
            evidence_by_memory,
        )
        if match is None:
            created = self._insert_item(
                connection,
                candidate,
                source_type=source_type,
                source_ref=source_ref,
                now=now,
            )
            action = (
                MemoryObservationAction.CREATED_ACTIVE
                if created.state == MemoryState.ACTIVE
                else MemoryObservationAction.CREATED_PENDING
            )
            return MemoryObservationResult(item=created, action=action)

        if has_conflicting_polarity(match["content"], candidate.content):
            connection.execute(
                "UPDATE memory_items SET state = ?, updated_at = ? WHERE id = ?",
                (MemoryState.SUPERSEDED.value, now.isoformat(), match["id"]),
            )
            created = self._insert_item(
                connection,
                candidate,
                source_type=source_type,
                source_ref=source_ref,
                now=now,
            )
            return MemoryObservationResult(
                item=created,
                action=MemoryObservationAction.SUPERSEDED,
            )

        evidence_added = self._insert_evidence(
            connection,
            memory_id=match["id"],
            source_ref=source_ref,
            excerpt=candidate.evidence_quote,
            now=now,
        )
        evidence_count = int(match["evidence_count"]) + int(evidence_added)
        current_state = MemoryState(match["state"])
        should_activate = (
            current_state == MemoryState.PENDING
            and (
                candidate.durability == MemoryDurability.EXPLICIT
                or evidence_count >= 2
            )
        )
        new_state = MemoryState.ACTIVE if should_activate else current_state
        activated_at = now.isoformat() if should_activate else match["activated_at"]
        topics = sorted(set(json.loads(match["topics_json"])) | set(candidate.topics))
        durability = (
            MemoryDurability.EXPLICIT.value
            if candidate.durability == MemoryDurability.EXPLICIT
            else match["durability"]
        )
        connection.execute(
            """
            UPDATE memory_items
            SET topics_json = ?, confidence = ?, durability = ?, state = ?,
                evidence_count = ?, last_observed_at = ?, activated_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(topics, ensure_ascii=False),
                max(float(match["confidence"]), candidate.confidence),
                durability,
                new_state.value,
                evidence_count,
                now.isoformat(),
                activated_at,
                now.isoformat(),
                match["id"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM memory_items WHERE id = ?", (match["id"],)
        ).fetchone()
        if should_activate:
            action = MemoryObservationAction.PROMOTED
        elif current_state == MemoryState.ACTIVE:
            action = MemoryObservationAction.UPDATED_ACTIVE
        else:
            action = MemoryObservationAction.UPDATED_PENDING
        return MemoryObservationResult(item=_row_to_memory(row), action=action)

    @staticmethod
    def _load_evidence(
        connection: sqlite3.Connection,
        rows: Sequence[sqlite3.Row],
    ) -> dict[str, list[str]]:
        if not rows:
            return {}
        placeholders = ",".join("?" for _ in rows)
        evidence_rows = connection.execute(
            f"""
            SELECT memory_id, user_excerpt FROM memory_evidence
            WHERE memory_id IN ({placeholders})
            """,
            tuple(row["id"] for row in rows),
        ).fetchall()
        result: dict[str, list[str]] = {}
        for evidence in evidence_rows:
            result.setdefault(evidence["memory_id"], []).append(
                evidence["user_excerpt"]
            )
        return result

    @staticmethod
    def _candidate_match_score(
        row: sqlite3.Row,
        candidate: MemoryCandidate,
        evidence_by_memory: dict[str, list[str]],
    ) -> float:
        score = text_similarity(row["content"], candidate.content)
        if memory_key_signatures_match(row["key"], candidate.key):
            score = max(score, 1.0)
        evidence_score = max(
            (
                memory_evidence_similarity(excerpt, candidate.evidence_quote)
                for excerpt in evidence_by_memory.get(row["id"], [])
            ),
            default=0.0,
        )
        return max(score, evidence_score)

    @classmethod
    def _find_semantic_match(
        cls,
        rows: Sequence[sqlite3.Row],
        candidate: MemoryCandidate,
        evidence_by_memory: dict[str, list[str]],
    ) -> sqlite3.Row | None:
        matches = [
            row
            for row in rows
            if row["kind"] == candidate.kind.value
            and row["scope"] == candidate.scope.value
            and row["scope_value"] == candidate.scope_value
            and cls._candidate_match_score(row, candidate, evidence_by_memory)
            >= SIMILARITY_THRESHOLD
        ]
        if not matches:
            return None
        return max(
            matches,
            key=lambda row: cls._candidate_match_score(
                row,
                candidate,
                evidence_by_memory,
            ),
        )

    def _insert_item(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        source_type: MemorySourceType,
        source_ref: str,
        now: datetime,
    ) -> MemoryItem:
        state = (
            MemoryState.ACTIVE
            if candidate.durability == MemoryDurability.EXPLICIT
            else MemoryState.PENDING
        )
        activated_at = now if state == MemoryState.ACTIVE else None
        item_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO memory_items (
                id, key, kind, content, topics_json, confidence, durability,
                scope, scope_value, source_type, source_ref, state,
                evidence_count, first_observed_at, last_observed_at,
                activated_at, created_at, updated_at, last_injected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                candidate.key,
                candidate.kind.value,
                candidate.content,
                json.dumps(candidate.topics, ensure_ascii=False),
                candidate.confidence,
                candidate.durability.value,
                candidate.scope.value,
                candidate.scope_value,
                source_type.value,
                source_ref,
                state.value,
                1,
                now.isoformat(),
                now.isoformat(),
                activated_at.isoformat() if activated_at else None,
                now.isoformat(),
                now.isoformat(),
                None,
            ),
        )
        self._insert_evidence(
            connection,
            memory_id=item_id,
            source_ref=source_ref,
            excerpt=candidate.evidence_quote,
            now=now,
        )
        row = connection.execute(
            "SELECT * FROM memory_items WHERE id = ?", (item_id,)
        ).fetchone()
        return _row_to_memory(row)

    @staticmethod
    def _insert_evidence(
        connection: sqlite3.Connection,
        *,
        memory_id: str,
        source_ref: str,
        excerpt: str,
        now: datetime,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO memory_evidence (
                memory_id, source_ref, user_excerpt, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (memory_id, source_ref, excerpt, now.isoformat()),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _expire_pending(connection: sqlite3.Connection, now: datetime) -> int:
        cutoff = now - timedelta(days=PENDING_TTL_DAYS)
        cursor = connection.execute(
            """
            UPDATE memory_items SET state = ?, updated_at = ?
            WHERE state = ? AND last_observed_at < ?
            """,
            (
                MemoryState.EXPIRED.value,
                now.isoformat(),
                MemoryState.PENDING.value,
                cutoff.isoformat(),
            ),
        )
        return cursor.rowcount

    def expire_pending(self, now: datetime | None = None) -> int:
        self.initialize()
        effective_now = now or utc_now()
        with self.connect() as connection:
            return self._expire_pending(connection, effective_now)

    def list_memories(
        self,
        *,
        states: set[MemoryState] | None = None,
        include_inactive: bool = False,
    ) -> list[MemoryItem]:
        self.initialize()
        self.expire_pending()
        query = "SELECT * FROM memory_items"
        parameters: tuple[str, ...] = ()
        if states is not None:
            if not states:
                return []
            ordered_states = sorted(state.value for state in states)
            placeholders = ",".join("?" for _ in ordered_states)
            query += f" WHERE state IN ({placeholders})"
            parameters = tuple(ordered_states)
        elif not include_inactive:
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
                "SELECT * FROM memory_items WHERE id = ?", (str(memory_id),)
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError(str(memory_id))
        return _row_to_memory(row)

    def list_evidence(self, memory_id: UUID) -> list[MemoryEvidence]:
        self.initialize()
        self.get_memory(memory_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_evidence
                WHERE memory_id = ? ORDER BY created_at, id
                """,
                (str(memory_id),),
            ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def confirm(self, memory_id: UUID) -> MemoryItem:
        return self._set_active(memory_id, require_state=MemoryState.PENDING)

    def restore(self, memory_id: UUID) -> MemoryItem:
        return self._set_active(memory_id, require_state=MemoryState.DELETED)

    def _set_active(
        self, memory_id: UUID, *, require_state: MemoryState
    ) -> MemoryItem:
        self.initialize()
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ? AND state = ?",
                (str(memory_id), require_state.value),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(str(memory_id))
            conflict = connection.execute(
                """
                SELECT id FROM memory_items
                WHERE key = ? AND kind = ? AND state IN ('pending', 'active')
                  AND id != ?
                """,
                (row["key"], row["kind"], str(memory_id)),
            ).fetchone()
            if conflict is not None:
                connection.execute(
                    "UPDATE memory_items SET state = ?, updated_at = ? WHERE id = ?",
                    (MemoryState.SUPERSEDED.value, now.isoformat(), conflict["id"]),
                )
            connection.execute(
                """
                UPDATE memory_items
                SET state = ?, durability = ?, source_type = ?, confidence = 1.0,
                    activated_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    MemoryState.ACTIVE.value,
                    MemoryDurability.EXPLICIT.value,
                    MemorySourceType.USER.value,
                    now.isoformat(),
                    now.isoformat(),
                    str(memory_id),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (str(memory_id),)
            ).fetchone()
        return _row_to_memory(updated)

    def update_memory(self, memory_id: UUID, content: str) -> MemoryItem:
        self.initialize()
        now = utc_now()
        normalized_content = content.strip()
        if not normalized_content or len(normalized_content) > 500:
            raise ValueError("Memory 内容长度必须在 1 到 500 之间")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ? AND state != 'deleted'",
                (str(memory_id),),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(str(memory_id))
            conflict = connection.execute(
                """
                SELECT id FROM memory_items
                WHERE key = ? AND kind = ? AND state IN ('pending', 'active')
                  AND id != ?
                """,
                (row["key"], row["kind"], str(memory_id)),
            ).fetchone()
            if conflict is not None:
                connection.execute(
                    "UPDATE memory_items SET state = ?, updated_at = ? WHERE id = ?",
                    (MemoryState.SUPERSEDED.value, now.isoformat(), conflict["id"]),
                )
            connection.execute(
                """
                UPDATE memory_items
                SET content = ?, confidence = 1.0, durability = ?,
                    source_type = ?, state = ?, activated_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_content,
                    MemoryDurability.EXPLICIT.value,
                    MemorySourceType.USER.value,
                    MemoryState.ACTIVE.value,
                    now.isoformat(),
                    now.isoformat(),
                    str(memory_id),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (str(memory_id),)
            ).fetchone()
        return _row_to_memory(updated)

    def soft_delete(self, memory_id: UUID) -> MemoryItem:
        self.initialize()
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (str(memory_id),)
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError(str(memory_id))
            connection.execute(
                "UPDATE memory_items SET state = ?, updated_at = ? WHERE id = ?",
                (MemoryState.DELETED.value, now.isoformat(), str(memory_id)),
            )
            deleted = connection.execute(
                "SELECT * FROM memory_items WHERE id = ?", (str(memory_id),)
            ).fetchone()
        return _row_to_memory(deleted)

    def clear_live(self) -> int:
        self.initialize()
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_items SET state = ?, updated_at = ?
                WHERE state IN ('active', 'pending')
                """,
                (MemoryState.DELETED.value, now.isoformat()),
            )
            return cursor.rowcount

    def mark_injected(self, memory_ids: Sequence[UUID], injected_at: datetime) -> int:
        if not memory_ids:
            return 0
        self.initialize()
        placeholders = ",".join("?" for _ in memory_ids)
        parameters = (
            injected_at.isoformat(),
            MemoryState.ACTIVE.value,
            *(str(memory_id) for memory_id in memory_ids),
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE memory_items SET last_injected_at = ?
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
        durability=row["durability"],
        scope=row["scope"],
        scope_value=row["scope_value"],
        source_type=row["source_type"],
        source_ref=row["source_ref"],
        state=row["state"],
        evidence_count=row["evidence_count"],
        first_observed_at=datetime.fromisoformat(row["first_observed_at"]),
        last_observed_at=datetime.fromisoformat(row["last_observed_at"]),
        activated_at=(
            datetime.fromisoformat(row["activated_at"])
            if row["activated_at"] is not None
            else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_injected_at=(
            datetime.fromisoformat(row["last_injected_at"])
            if row["last_injected_at"] is not None
            else None
        ),
    )


def _row_to_evidence(row: sqlite3.Row) -> MemoryEvidence:
    return MemoryEvidence(
        id=row["id"],
        memory_id=UUID(row["memory_id"]),
        source_ref=row["source_ref"],
        user_excerpt=row["user_excerpt"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
