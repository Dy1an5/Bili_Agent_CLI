from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

from bili_agent_cli.agent.memory.models import (
    MemoryCandidate,
    MemoryDurability,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
)
from bili_agent_cli.agent.memory.store import MemoryStore
from bili_agent_cli.content.models import (
    FavoriteVideoContext,
    HistoryVideoContext,
    VideoAuthor,
    VideoContexts,
    VideoDetail,
    VideoIdentity,
    VideoRecord,
)
from bili_agent_cli.content.store import ContentStore
from bili_agent_cli.persona.models import (
    ClassifiedTopic,
    GetUserProfileArgs,
    OpenTag,
    VideoTopicClassification,
    VideoTopicClassificationBatch,
)
from bili_agent_cli.persona.service import PersonaService, _decay
from bili_agent_cli.schemas.favorites import FavoriteFolder


NOW = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)


def video(
    *,
    bvid: str,
    cid: str,
    contexts: VideoContexts,
    duration: int = 600,
) -> VideoRecord:
    return VideoRecord(
        identity=VideoIdentity(bvid=bvid, cid=cid),
        author=VideoAuthor(mid="10", name="测试UP主"),
        detail=VideoDetail(
            title="人工智能模型教程",
            description="Python 和大模型",
            duration_seconds=duration,
            is_default_part=True,
        ),
        contexts=contexts,
        observed_at=NOW,
    )


def folder(folder_id: str) -> FavoriteFolder:
    return FavoriteFolder(
        id=folder_id,
        title="AI 收藏",
        cover_url=None,
        media_count=1,
    )


class FakeClassifier:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, inputs, *, on_usage=None):
        del on_usage
        self.calls.append([item.source_id for item in inputs])
        return VideoTopicClassificationBatch(
            results=[
                VideoTopicClassification(
                    source_id=item.source_id,
                    topics=[
                        ClassifiedTopic(
                            topic_key="technology.ai",
                            confidence=0.8,
                            evidence="标题包含人工智能",
                        )
                    ],
                    tags=[OpenTag(label="大模型", confidence=0.8)],
                )
                for item in inputs
            ]
        )


def test_profile_refresh_is_incremental_and_merges_explicit_memory(tmp_path) -> None:
    content = ContentStore(tmp_path / "content.db")
    memory = MemoryStore(tmp_path / "memory.db")
    record = video(
        bvid="BV1persona",
        cid="101",
        contexts=VideoContexts(
            favorite=FavoriteVideoContext(
                folder_id="10",
                folder_title="AI 收藏",
                favorited_at=NOW,
            )
        ),
    )
    content.save_batch(
        [record], source="favorite", source_scope="10", folder=folder("10")
    )
    content.save_batch(
        [
            record.model_copy(
                update={
                    "contexts": VideoContexts(
                        favorite=FavoriteVideoContext(
                            folder_id="20",
                            folder_title="另一个收藏夹",
                            favorited_at=NOW,
                        )
                    )
                }
            )
        ],
        source="favorite",
        source_scope="20",
        folder=folder("20"),
    )
    memory.remember(
        MemoryCandidate(
            key="recommendation.no_intro",
            kind=MemoryKind.CONSTRAINT,
            content="不要推荐入门视频",
            topics=["人工智能"],
            confidence=1,
            evidence_quote="不要推荐入门视频",
            durability=MemoryDurability.EXPLICIT,
            scope=MemoryScope.GLOBAL,
            scope_value=None,
        ),
        source_type=MemorySourceType.USER,
        source_ref="manual:test",
    )
    classifier = FakeClassifier()
    service = PersonaService(
        content,
        memory,
        classifier=classifier,
        now=lambda: NOW,
    )

    first = asyncio.run(service.get_profile(GetUserProfileArgs()))
    second = asyncio.run(service.get_profile(GetUserProfileArgs()))
    cached = asyncio.run(
        service.get_profile(GetUserProfileArgs(refresh=False))
    )

    assert len(classifier.calls) == 1
    assert first.status == "complete"
    assert first.coverage.eligible_video_count == 1
    assert first.coverage.classified_video_count == 1
    assert first.inferred.topics[0].topic_key == "technology.ai"
    assert first.inferred.topics[0].score == 28.3
    assert first.explicit_preferences[0].content == "不要推荐入门视频"
    assert second.snapshot_version == 2
    assert cached.snapshot_version == 2
    with content.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM user_content_events WHERE source='favorite'"
        ).fetchone()[0] == 1
        creator = connection.execute(
            """
            SELECT raw_score, last_evidence_at
            FROM creator_preference_scores WHERE author_mid='10'
            """
        ).fetchone()
        assert creator["raw_score"] == 5.0
        assert creator["last_evidence_at"] is not None


def test_history_weights_do_not_treat_low_progress_as_negative(tmp_path) -> None:
    content = ContentStore(tmp_path / "content.db")
    memory = MemoryStore(tmp_path / "memory.db")
    records = [
        video(
            bvid=f"BV1history{index}",
            cid=str(200 + index),
            contexts=VideoContexts(
                history=HistoryVideoContext(
                    viewed_at=NOW,
                    progress_seconds=progress,
                    is_favorite=False,
                )
            ),
        )
        for index, progress in enumerate((50, 100, 500, -1))
    ]
    content.save_batch(records, source="history")
    service = PersonaService(
        content,
        memory,
        classifier=FakeClassifier(),
        now=lambda: NOW,
    )

    asyncio.run(service.get_profile(GetUserProfileArgs()))

    with content.connect() as connection:
        weights = [
            row[0]
            for row in connection.execute(
                """
                SELECT weight FROM user_content_events
                WHERE source='history' ORDER BY bvid
                """
            ).fetchall()
        ]
        negative = connection.execute(
            "SELECT sum(negative_evidence_count) FROM topic_preference_scores"
        ).fetchone()[0]
    assert weights == [0.0, 0.5, 1.5, 3.0]
    assert negative == 0


def test_time_decay_uses_configured_half_life() -> None:
    assert math.isclose(_decay(90, 90), 0.5)
    assert math.isclose(_decay(180, 90), 0.25)
    assert _decay(-1, 90) == 1.0
