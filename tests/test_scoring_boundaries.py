from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bili_agent_cli.content.models import VideoIdentity
from bili_agent_cli.content.scoring import (
    ContentEventType,
    ScoreComponent,
    TopicCandidate,
    TopicSource,
    UserContentEvent,
    VideoScoreResult,
)
from bili_agent_cli.content.store import CONTENT_SCHEMA_VERSION, ContentStore


SCORING_TABLES = {
    "topics",
    "video_topics",
    "user_content_events",
    "topic_preference_scores",
    "video_topic_classification_state",
    "event_topics",
    "creator_preference_scores",
    "profile_feature_scores",
}


def table_names(store: ContentStore) -> set[str]:
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row["name"]) for row in rows}


def test_new_database_initializes_empty_scoring_tables(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")

    store.initialize()

    assert SCORING_TABLES <= table_names(store)
    with store.connect() as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == CONTENT_SCHEMA_VERSION
        )
        for table in SCORING_TABLES:
            assert connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0


def test_v1_database_migrates_without_losing_video_rows(tmp_path) -> None:
    store = ContentStore(tmp_path / "content.db")
    with store.connect() as connection:
        store._create_schema_v1(connection)
        connection.execute(
            """
            INSERT INTO videos (
                bvid, cid, title, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "BV1preserved",
                "123",
                "保留的视频",
                "2026-09-14T00:00:00+00:00",
                "2026-09-14T00:00:00+00:00",
            ),
        )
        connection.execute("PRAGMA user_version = 1")

    store.initialize()

    assert SCORING_TABLES <= table_names(store)
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT title FROM videos WHERE bvid = 'BV1preserved'"
            ).fetchone()["title"]
            == "保留的视频"
        )
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == CONTENT_SCHEMA_VERSION
        )


def test_v2_database_migrates_to_persona_schema_without_losing_scores(
    tmp_path,
) -> None:
    store = ContentStore(tmp_path / "content.db")
    with store.connect() as connection:
        store._create_schema_v1(connection)
        store._migrate_v1_to_v2(connection)
        connection.execute(
            """
            INSERT INTO topics (
                topic_key, label, source_type, created_at, updated_at
            ) VALUES ('technology.ai', '人工智能', 'code', 'now', 'now')
            """
        )
        topic_id = connection.execute(
            "SELECT id FROM topics WHERE topic_key='technology.ai'"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO topic_preference_scores (
                topic_id, score, positive_evidence_count,
                negative_evidence_count, algorithm_version, updated_at
            ) VALUES (?, 42, 2, 0, 'old-v1', 'now')
            """,
            (topic_id,),
        )
        connection.execute("PRAGMA user_version = 2")

    store.initialize()

    assert SCORING_TABLES <= table_names(store)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT score, raw_score FROM topic_preference_scores"
        ).fetchone()
        assert tuple(row) == (42.0, 0.0)
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == CONTENT_SCHEMA_VERSION
        )


def test_scoring_models_define_boundaries_without_computing_scores() -> None:
    identity = VideoIdentity(bvid="BV1score", cid="88")
    candidate = TopicCandidate(
        topic_key="technology.ai",
        label="人工智能",
        confidence=0.8,
        source=TopicSource.MODEL,
        evidence="仅作为未来推断结果的输入边界",
    )
    event = UserContentEvent(
        event_type=ContentEventType.FAVORITE,
        video=identity,
        source="favorite",
        weight=1.0,
        occurred_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        metadata={"folder_id": "10"},
    )
    result = VideoScoreResult(
        video=identity,
        score=0.75,
        components=[
            ScoreComponent(name=candidate.topic_key, value=0.75)
        ],
        algorithm_version="reserved-v1",
    )

    assert event.video == identity
    assert result.components[0].name == "technology.ai"

    with pytest.raises(ValidationError):
        TopicCandidate(
            topic_key="invalid",
            label="无效置信度",
            confidence=1.1,
            source=TopicSource.CODE,
        )
