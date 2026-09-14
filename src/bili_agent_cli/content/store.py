from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bili_agent_cli.content.cid_resolver import CidResolutionFailure
from bili_agent_cli.profile import PRIVACY_DIR
from bili_agent_cli.schemas.favorites import FavoriteFolder
from bili_agent_cli.schemas.search import SearchVideoQuery

from .models import VideoRecord


CONTENT_DB_PATH = PRIVACY_DIR / "content.db"
CONTENT_SCHEMA_VERSION = 2


class ContentStorageError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class ContentStore:
    def __init__(self, path: Path = CONTENT_DB_PATH) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
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
            if connection is not None:
                connection.rollback()
            raise ContentStorageError(str(error)) from error
        finally:
            if connection is not None:
                connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > CONTENT_SCHEMA_VERSION:
                raise ContentStorageError("内容数据库版本高于当前程序")
            if version == 0:
                self._create_schema_v1(connection)
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version == 1:
                self._migrate_v1_to_v2(connection)
                connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def _create_schema_v1(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE videos (
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                aid TEXT,
                title TEXT,
                description TEXT,
                cover_url TEXT,
                duration_seconds INTEGER CHECK (
                    duration_seconds IS NULL OR duration_seconds >= 0
                ),
                published_at TEXT,
                author_mid TEXT,
                author_name TEXT,
                author_avatar_url TEXT,
                is_default INTEGER CHECK (
                    is_default IS NULL OR is_default IN (0, 1)
                ),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (bvid, cid)
            );

            CREATE UNIQUE INDEX idx_videos_default_part
            ON videos(bvid) WHERE is_default = 1;

            CREATE TABLE video_feedback (
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                source TEXT NOT NULL,
                views INTEGER CHECK (views IS NULL OR views >= 0),
                danmaku INTEGER CHECK (danmaku IS NULL OR danmaku >= 0),
                favorites INTEGER CHECK (favorites IS NULL OR favorites >= 0),
                replies INTEGER CHECK (replies IS NULL OR replies >= 0),
                likes INTEGER CHECK (likes IS NULL OR likes >= 0),
                observed_at TEXT NOT NULL,
                PRIMARY KEY (bvid, cid, source),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE favorite_folders (
                folder_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                cover_url TEXT,
                media_count INTEGER NOT NULL CHECK (media_count >= 0),
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE favorite_items (
                folder_id TEXT NOT NULL REFERENCES favorite_folders(folder_id),
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                favorited_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                removed_at TEXT,
                PRIMARY KEY (folder_id, bvid, cid),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE watch_later_items (
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                progress_seconds INTEGER,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                removed_at TEXT,
                PRIMARY KEY (bvid, cid),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE dynamic_items (
                source_scope TEXT NOT NULL,
                dynamic_id TEXT NOT NULL,
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                dynamic_published_at TEXT,
                publisher_mid TEXT,
                publisher_name TEXT,
                is_pinned INTEGER CHECK (
                    is_pinned IS NULL OR is_pinned IN (0, 1)
                ),
                likes INTEGER CHECK (likes IS NULL OR likes >= 0),
                replies INTEGER CHECK (replies IS NULL OR replies >= 0),
                reposts INTEGER CHECK (reposts IS NULL OR reposts >= 0),
                favorites INTEGER CHECK (favorites IS NULL OR favorites >= 0),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source_scope, dynamic_id, bvid, cid),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE history_items (
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                viewed_at TEXT NOT NULL,
                progress_seconds INTEGER NOT NULL,
                is_favorite INTEGER CHECK (
                    is_favorite IS NULL OR is_favorite IN (0, 1)
                ),
                observed_at TEXT NOT NULL,
                PRIMARY KEY (bvid, cid, viewed_at),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE search_runs (
                id TEXT PRIMARY KEY,
                keyword TEXT NOT NULL,
                page INTEGER NOT NULL,
                page_size INTEGER NOT NULL,
                search_order TEXT NOT NULL,
                duration INTEGER NOT NULL,
                tid INTEGER,
                published_after TEXT,
                published_before TEXT,
                searched_at TEXT NOT NULL
            );

            CREATE TABLE search_results (
                search_run_id TEXT NOT NULL REFERENCES search_runs(id)
                    ON DELETE CASCADE,
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                rank INTEGER NOT NULL CHECK (rank >= 1),
                PRIMARY KEY (search_run_id, bvid, cid),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE ingestion_failures (
                source TEXT NOT NULL,
                source_scope TEXT NOT NULL,
                item_key TEXT NOT NULL,
                bvid TEXT NOT NULL,
                reason TEXT NOT NULL,
                upstream_status INTEGER,
                upstream_code INTEGER,
                occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 1),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source, source_scope, item_key, bvid)
            );

            CREATE TABLE sync_states (
                source TEXT NOT NULL,
                source_scope TEXT NOT NULL,
                status TEXT NOT NULL,
                last_started_at TEXT NOT NULL,
                last_succeeded_at TEXT,
                cursor_json TEXT,
                error_message TEXT,
                PRIMARY KEY (source, source_scope)
            );
        """)

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE topics (
                id INTEGER PRIMARY KEY,
                topic_key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                description TEXT,
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE video_topics (
                bvid TEXT NOT NULL,
                cid TEXT NOT NULL,
                topic_id INTEGER NOT NULL REFERENCES topics(id)
                    ON DELETE CASCADE,
                confidence REAL NOT NULL CHECK (
                    confidence >= 0 AND confidence <= 1
                ),
                source_type TEXT NOT NULL,
                evidence TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (bvid, cid, topic_id, source_type),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE CASCADE
            );

            CREATE TABLE user_content_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                bvid TEXT,
                cid TEXT,
                source TEXT NOT NULL,
                weight REAL,
                occurred_at TEXT NOT NULL,
                metadata_json TEXT,
                CHECK (
                    (bvid IS NULL AND cid IS NULL)
                    OR (bvid IS NOT NULL AND cid IS NOT NULL)
                ),
                FOREIGN KEY (bvid, cid) REFERENCES videos(bvid, cid)
                    ON DELETE SET NULL
            );

            CREATE TABLE topic_preference_scores (
                topic_id INTEGER PRIMARY KEY REFERENCES topics(id)
                    ON DELETE CASCADE,
                score REAL NOT NULL,
                positive_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (
                    positive_evidence_count >= 0
                ),
                negative_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (
                    negative_evidence_count >= 0
                ),
                algorithm_version TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

    def get_default_cid(self, bvid: str) -> str | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cid FROM videos WHERE bvid = ? AND is_default = 1",
                (bvid,),
            ).fetchone()
        return str(row["cid"]) if row is not None else None

    def save_folders(self, folders: Sequence[FavoriteFolder]) -> None:
        self.initialize()
        observed_at = utc_now()
        with self.connect() as connection:
            for folder in folders:
                self._upsert_folder(connection, folder, observed_at)

    def save_batch(
        self,
        records: Sequence[VideoRecord],
        *,
        source: str,
        source_scope: str = "default",
        failures: Sequence[CidResolutionFailure] = (),
        folder: FavoriteFolder | None = None,
        search_query: SearchVideoQuery | None = None,
        started_at: datetime | None = None,
    ) -> str | None:
        self.initialize()
        now = utc_now()
        started = started_at or now
        search_run_id = str(uuid4()) if search_query is not None else None
        status = "partial" if failures else "completed"

        with self.connect() as connection:
            if folder is not None:
                self._upsert_folder(connection, folder, now)
            if search_query is not None and search_run_id is not None:
                self._insert_search_run(
                    connection, search_run_id, search_query, now
                )

            for record in records:
                self._upsert_video(connection, record)
                self._upsert_feedback(connection, record, source)
                self._upsert_contexts(
                    connection,
                    record,
                    source_scope=source_scope,
                    search_run_id=search_run_id,
                )

            for failure in failures:
                connection.execute(
                    """
                    INSERT INTO ingestion_failures (
                        source, source_scope, item_key, bvid, reason,
                        upstream_status, upstream_code, occurrence_count,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(source, source_scope, item_key, bvid) DO UPDATE SET
                        reason = excluded.reason,
                        upstream_status = excluded.upstream_status,
                        upstream_code = excluded.upstream_code,
                        occurrence_count = ingestion_failures.occurrence_count + 1,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        source,
                        source_scope,
                        failure.key,
                        failure.bvid,
                        failure.reason,
                        failure.upstream_status,
                        failure.upstream_code,
                        _iso(now),
                        _iso(now),
                    ),
                )

            connection.execute(
                """
                INSERT INTO sync_states (
                    source, source_scope, status, last_started_at,
                    last_succeeded_at, cursor_json, error_message
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(source, source_scope) DO UPDATE SET
                    status = excluded.status,
                    last_started_at = excluded.last_started_at,
                    last_succeeded_at = excluded.last_succeeded_at,
                    error_message = NULL
                """,
                (source, source_scope, status, _iso(started), _iso(now)),
            )
        return search_run_id

    @staticmethod
    def _upsert_video(
        connection: sqlite3.Connection, record: VideoRecord
    ) -> None:
        identity = record.identity
        detail = record.detail
        author = record.author
        observed_at = _iso(record.observed_at)
        connection.execute(
            """
            INSERT INTO videos (
                bvid, cid, aid, title, description, cover_url,
                duration_seconds, published_at, author_mid, author_name,
                author_avatar_url, is_default, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bvid, cid) DO UPDATE SET
                aid = COALESCE(excluded.aid, videos.aid),
                title = COALESCE(excluded.title, videos.title),
                description = COALESCE(excluded.description, videos.description),
                cover_url = COALESCE(excluded.cover_url, videos.cover_url),
                duration_seconds = COALESCE(
                    excluded.duration_seconds, videos.duration_seconds
                ),
                published_at = COALESCE(excluded.published_at, videos.published_at),
                author_mid = COALESCE(excluded.author_mid, videos.author_mid),
                author_name = COALESCE(excluded.author_name, videos.author_name),
                author_avatar_url = COALESCE(
                    excluded.author_avatar_url, videos.author_avatar_url
                ),
                is_default = COALESCE(excluded.is_default, videos.is_default),
                last_seen_at = excluded.last_seen_at
            """,
            (
                identity.bvid,
                identity.cid,
                detail.aid,
                detail.title,
                detail.description,
                detail.cover_url,
                detail.duration_seconds,
                _iso(detail.published_at),
                author.mid,
                author.name,
                author.avatar_url,
                None if detail.is_default_part is None else int(detail.is_default_part),
                observed_at,
                observed_at,
            ),
        )

    @staticmethod
    def _upsert_feedback(
        connection: sqlite3.Connection,
        record: VideoRecord,
        source: str,
    ) -> None:
        feedback = record.feedback
        connection.execute(
            """
            INSERT INTO video_feedback (
                bvid, cid, source, views, danmaku, favorites,
                replies, likes, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bvid, cid, source) DO UPDATE SET
                views = COALESCE(excluded.views, video_feedback.views),
                danmaku = COALESCE(excluded.danmaku, video_feedback.danmaku),
                favorites = COALESCE(excluded.favorites, video_feedback.favorites),
                replies = COALESCE(excluded.replies, video_feedback.replies),
                likes = COALESCE(excluded.likes, video_feedback.likes),
                observed_at = excluded.observed_at
            """,
            (
                record.identity.bvid,
                record.identity.cid,
                source,
                feedback.views,
                feedback.danmaku,
                feedback.favorites,
                feedback.replies,
                feedback.likes,
                _iso(record.observed_at),
            ),
        )

    @staticmethod
    def _upsert_folder(
        connection: sqlite3.Connection,
        folder: FavoriteFolder,
        observed_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO favorite_folders (
                folder_id, title, cover_url, media_count, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(folder_id) DO UPDATE SET
                title = excluded.title,
                cover_url = COALESCE(
                    excluded.cover_url, favorite_folders.cover_url
                ),
                media_count = excluded.media_count,
                last_seen_at = excluded.last_seen_at
            """,
            (
                folder.id,
                folder.title,
                folder.cover_url,
                folder.media_count,
                _iso(observed_at),
            ),
        )

    @staticmethod
    def _insert_search_run(
        connection: sqlite3.Connection,
        run_id: str,
        query: SearchVideoQuery,
        observed_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO search_runs (
                id, keyword, page, page_size, search_order, duration, tid,
                published_after, published_before, searched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                query.keyword,
                query.page,
                query.page_size,
                query.order.value,
                int(query.duration),
                query.tid,
                _iso(query.published_after),
                _iso(query.published_before),
                _iso(observed_at),
            ),
        )

    @staticmethod
    def _upsert_contexts(
        connection: sqlite3.Connection,
        record: VideoRecord,
        *,
        source_scope: str,
        search_run_id: str | None,
    ) -> None:
        bvid = record.identity.bvid
        cid = record.identity.cid
        observed_at = _iso(record.observed_at)
        contexts = record.contexts

        if contexts.favorite is not None:
            context = contexts.favorite
            connection.execute(
                """
                INSERT INTO favorite_items (
                    folder_id, bvid, cid, favorited_at,
                    first_seen_at, last_seen_at, removed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(folder_id, bvid, cid) DO UPDATE SET
                    favorited_at = COALESCE(
                        excluded.favorited_at, favorite_items.favorited_at
                    ),
                    last_seen_at = excluded.last_seen_at,
                    removed_at = NULL
                """,
                (
                    context.folder_id,
                    bvid,
                    cid,
                    _iso(context.favorited_at),
                    observed_at,
                    observed_at,
                ),
            )

        if contexts.watch_later is not None:
            connection.execute(
                """
                INSERT INTO watch_later_items (
                    bvid, cid, progress_seconds,
                    first_seen_at, last_seen_at, removed_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(bvid, cid) DO UPDATE SET
                    progress_seconds = COALESCE(
                        excluded.progress_seconds,
                        watch_later_items.progress_seconds
                    ),
                    last_seen_at = excluded.last_seen_at,
                    removed_at = NULL
                """,
                (
                    bvid,
                    cid,
                    contexts.watch_later.progress_seconds,
                    observed_at,
                    observed_at,
                ),
            )

        if contexts.dynamic is not None:
            context = contexts.dynamic
            feedback = context.feedback
            publisher = context.publisher
            connection.execute(
                """
                INSERT INTO dynamic_items (
                    source_scope, dynamic_id, bvid, cid,
                    dynamic_published_at, publisher_mid, publisher_name,
                    is_pinned, likes, replies, reposts, favorites,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_scope, dynamic_id, bvid, cid) DO UPDATE SET
                    dynamic_published_at = COALESCE(
                        excluded.dynamic_published_at,
                        dynamic_items.dynamic_published_at
                    ),
                    publisher_mid = COALESCE(
                        excluded.publisher_mid, dynamic_items.publisher_mid
                    ),
                    publisher_name = COALESCE(
                        excluded.publisher_name, dynamic_items.publisher_name
                    ),
                    is_pinned = COALESCE(
                        excluded.is_pinned, dynamic_items.is_pinned
                    ),
                    likes = COALESCE(excluded.likes, dynamic_items.likes),
                    replies = COALESCE(excluded.replies, dynamic_items.replies),
                    reposts = COALESCE(excluded.reposts, dynamic_items.reposts),
                    favorites = COALESCE(
                        excluded.favorites, dynamic_items.favorites
                    ),
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    source_scope,
                    context.dynamic_id,
                    bvid,
                    cid,
                    _iso(context.published_at),
                    publisher.mid if publisher else None,
                    publisher.name if publisher else None,
                    None if context.is_pinned is None else int(context.is_pinned),
                    feedback.likes,
                    feedback.replies,
                    feedback.reposts,
                    feedback.favorites,
                    observed_at,
                    observed_at,
                ),
            )

        if contexts.history is not None:
            context = contexts.history
            connection.execute(
                """
                INSERT INTO history_items (
                    bvid, cid, viewed_at, progress_seconds,
                    is_favorite, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bvid, cid, viewed_at) DO UPDATE SET
                    progress_seconds = excluded.progress_seconds,
                    is_favorite = COALESCE(
                        excluded.is_favorite, history_items.is_favorite
                    ),
                    observed_at = excluded.observed_at
                """,
                (
                    bvid,
                    cid,
                    _iso(context.viewed_at),
                    context.progress_seconds,
                    None if context.is_favorite is None else int(context.is_favorite),
                    observed_at,
                ),
            )

        if contexts.search is not None:
            if search_run_id is None:
                raise sqlite3.IntegrityError("搜索上下文缺少 search_run")
            connection.execute(
                """
                INSERT INTO search_results (
                    search_run_id, bvid, cid, rank
                ) VALUES (?, ?, ?, ?)
                """,
                (search_run_id, bvid, cid, contexts.search.rank),
            )
