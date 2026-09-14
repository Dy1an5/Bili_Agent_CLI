from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bili_agent_cli.content.cid_resolver import CidResolutionFailure
from bili_agent_cli.content.models import (
    FavoriteVideoContext,
    SearchVideoContext,
    VideoContexts,
    VideoDetail,
    VideoFeedback,
    VideoIdentity,
    VideoRecord,
)
from bili_agent_cli.content.store import (
    CONTENT_SCHEMA_VERSION,
    ContentStorageError,
    ContentStore,
)
from bili_agent_cli.schemas.favorites import FavoriteFolder
from bili_agent_cli.schemas.search import SearchVideoQuery


NOW = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)


def record(
    *,
    bvid: str = "BV1content1",
    cid: str = "101",
    title: str | None = "标题",
    views: int | None = 12,
    folder_id: str | None = None,
    rank: int | None = None,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=bvid, cid=cid),
        detail=VideoDetail(
            title=title,
            duration_seconds=60,
            is_default_part=cid == "101",
        ),
        feedback=VideoFeedback(views=views),
        contexts=VideoContexts(
            favorite=(
                FavoriteVideoContext(
                    folder_id=folder_id,
                    folder_title="收藏夹",
                    favorited_at=NOW,
                )
                if folder_id is not None
                else None
            ),
            search=(
                SearchVideoContext(
                    keyword="测试",
                    page=1,
                    rank=rank,
                    order="totalrank",
                    duration=0,
                )
                if rank is not None
                else None
            ),
        ),
        observed_at=NOW,
    )


def folder(folder_id: str) -> FavoriteFolder:
    return FavoriteFolder(
        id=folder_id,
        title=f"收藏夹 {folder_id}",
        cover_url=None,
        media_count=1,
    )


def scalar(store: ContentStore, sql: str) -> object:
    with store.connect() as connection:
        return connection.execute(sql).fetchone()[0]


def test_initialize_schema_version_and_private_permissions(tmp_path) -> None:
    database = tmp_path / "privacy" / "content.db"
    store = ContentStore(database)

    store.initialize()

    assert scalar(store, "PRAGMA user_version") == CONTENT_SCHEMA_VERSION
    assert database.stat().st_mode & 0o777 == 0o600
    assert database.parent.stat().st_mode & 0o777 == 0o700
    with store.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_compound_identity_and_multiple_favorite_folders(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    store.save_batch(
        [record(folder_id="10")],
        source="favorite",
        source_scope="10",
        folder=folder("10"),
    )
    store.save_batch(
        [record(folder_id="20")],
        source="favorite",
        source_scope="20",
        folder=folder("20"),
    )
    store.save_batch(
        [record(cid="202", title="第二 P")],
        source="watch_later",
    )

    assert scalar(store, "SELECT count(*) FROM videos") == 2
    assert scalar(store, "SELECT count(*) FROM favorite_items") == 2
    assert store.get_default_cid("BV1content1") == "101"


def test_upsert_keeps_old_value_for_null_but_accepts_zero(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    store.save_batch([record(title="旧标题", views=99)], source="search")
    store.save_batch([record(title=None, views=0)], source="search")

    with store.connect() as connection:
        video = connection.execute(
            "SELECT title FROM videos WHERE bvid = ? AND cid = ?",
            ("BV1content1", "101"),
        ).fetchone()
        feedback = connection.execute(
            "SELECT views FROM video_feedback WHERE bvid = ? AND cid = ?",
            ("BV1content1", "101"),
        ).fetchone()
    assert video["title"] == "旧标题"
    assert feedback["views"] == 0


def test_search_run_and_partial_failure_are_persisted(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    query = SearchVideoQuery(keyword="测试")
    failure = CidResolutionFailure(
        key="search:2",
        bvid="BV1failed1",
        reason="详情接口失败",
        upstream_status=503,
        upstream_code=-1,
    )

    run_id = store.save_batch(
        [record(rank=1)],
        source="search",
        source_scope="测试",
        search_query=query,
        failures=[failure],
    )
    store.save_batch(
        [],
        source="search",
        source_scope="测试",
        failures=[failure],
    )

    assert run_id is not None
    assert scalar(store, "SELECT count(*) FROM search_runs") == 1
    assert scalar(store, "SELECT count(*) FROM search_results") == 1
    assert scalar(store, "SELECT occurrence_count FROM ingestion_failures") == 2
    with store.connect() as connection:
        state = connection.execute(
            "SELECT status FROM sync_states WHERE source = 'search'"
        ).fetchone()
    assert state["status"] == "partial"


def test_batch_rolls_back_when_a_relationship_is_invalid(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    store.initialize()

    with pytest.raises(ContentStorageError):
        store.save_batch(
            [record(folder_id="missing")],
            source="favorite",
            source_scope="missing",
        )

    assert scalar(store, "SELECT count(*) FROM videos") == 0
    assert scalar(store, "SELECT count(*) FROM video_feedback") == 0


def test_connection_errors_are_wrapped(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    store.initialize()

    with pytest.raises(ContentStorageError):
        with store.connect() as connection:
            connection.execute("INSERT INTO videos (bvid) VALUES ('invalid')")

    assert scalar(store, "SELECT count(*) FROM videos") == 0
