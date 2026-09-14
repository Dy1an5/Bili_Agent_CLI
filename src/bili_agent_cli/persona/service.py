from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bili_agent_cli.agent.memory.models import MemoryKind, MemoryState
from bili_agent_cli.agent.memory.service import SENSITIVE_MEMORY_PATTERN
from bili_agent_cli.agent.memory.store import MemoryStore
from bili_agent_cli.agent.models import TokenUsage
from bili_agent_cli.content.models import (
    VideoAuthor,
    VideoContexts,
    VideoDetail,
    VideoIdentity,
    VideoRecord,
)
from bili_agent_cli.content.store import ContentStore
from bili_agent_cli.content.time import BEIJING_TIMEZONE

from .classifier import (
    CLASSIFIER_VERSION,
    PROMPT_VERSION,
    ClassificationInput,
    classify_topics,
)
from .models import (
    CreatorPreference,
    DurationPreference,
    EmergingTag,
    ExplicitPreference,
    GetUserProfileArgs,
    InferredProfile,
    PersonaCoverage,
    PersonaReliability,
    PersonaStatus,
    PreferenceLevel,
    TopicPreference,
    UserProfileResponse,
    VideoTopicClassificationBatch,
)
from .taxonomy import (
    TAXONOMY_BY_KEY,
    TAXONOMY_VERSION,
    TAXONOMY_TOPICS,
)


ALGORITHM_VERSION = "persona-score-v1"
Classifier = Callable[..., Awaitable[VideoTopicClassificationBatch]]
_refresh_lock = asyncio.Lock()


class PersonaError(Exception):
    pass


@dataclass(frozen=True)
class EligibleVideo:
    record: VideoRecord
    input: ClassificationInput
    input_hash: str
    priority: int
    latest_event_at: datetime


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _event_id(source_ref: str) -> str:
    return hashlib.sha256(source_ref.encode("utf-8")).hexdigest()


def _input_hash(value: ClassificationInput) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    return re.sub(r"\s+", " ", normalized)


def _decay(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (max(0.0, age_days) / half_life_days)


def _normalized_score(raw_score: float) -> float:
    return 100.0 * (1.0 - math.exp(-raw_score / 12.0))


def _preference_level(score: float) -> PreferenceLevel | None:
    if score >= 70:
        return PreferenceLevel.STRONG
    if score >= 40:
        return PreferenceLevel.MEDIUM
    if score >= 20:
        return PreferenceLevel.EXPLORATORY
    return None


def _duration_bucket(seconds: int) -> tuple[str, str]:
    if seconds < 300:
        return "under_5m", "5 分钟以内"
    if seconds < 1_200:
        return "5_to_20m", "5～20 分钟"
    if seconds < 3_600:
        return "20_to_60m", "20～60 分钟"
    return "over_60m", "60 分钟以上"


class PersonaService:
    def __init__(
        self,
        content_store: ContentStore | None = None,
        memory_store: MemoryStore | None = None,
        *,
        classifier: Classifier = classify_topics,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.content_store = content_store or ContentStore()
        self.memory_store = memory_store or MemoryStore()
        self.classifier = classifier
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def get_profile(
        self,
        args: GetUserProfileArgs,
        *,
        on_usage: Callable[[TokenUsage], None] | None = None,
    ) -> UserProfileResponse:
        if not args.refresh:
            snapshot = self.memory_store.latest_persona_snapshot()
            if snapshot is not None:
                return UserProfileResponse.model_validate(snapshot)
            return self._empty_profile()

        async with _refresh_lock:
            return await self._refresh(args, on_usage=on_usage)

    async def _refresh(
        self,
        args: GetUserProfileArgs,
        *,
        on_usage: Callable[[TokenUsage], None] | None,
    ) -> UserProfileResponse:
        run_id = self.memory_store.start_persona_refresh(
            {"content_db": True, "memory_db": True, "external_sync": False}
        )
        errors: list[str] = []
        try:
            self.content_store.initialize()
            self._seed_taxonomy()
            self._derive_events()
            eligible = self._eligible_videos()
            pending = self._pending_videos(eligible)[: args.max_new_videos]
            for start in range(0, len(pending), 20):
                batch = pending[start : start + 20]
                try:
                    result = await self.classifier(
                        [item.input for item in batch],
                        on_usage=on_usage,
                    )
                    self._save_video_classifications(batch, result)
                except Exception as error:
                    errors.append(error.__class__.__name__)
                    self._mark_classification_failures(
                        batch, error.__class__.__name__
                    )

            try:
                await self._classify_search_events(on_usage=on_usage)
            except Exception as error:
                errors.append(error.__class__.__name__)

            profile = self._build_profile(eligible, errors)
            stored = profile.model_dump(
                mode="json",
                exclude_computed_fields=True,
            )
            version = self.memory_store.save_persona_snapshot(stored)
            profile = profile.model_copy(update={"snapshot_version": version})
            self.memory_store.finish_persona_refresh(
                run_id,
                status=profile.status.value,
                counts={
                    "eligible_videos": profile.coverage.eligible_video_count,
                    "classified_videos": profile.coverage.classified_video_count,
                    "remaining": profile.coverage.remaining_unclassified,
                },
                errors=errors,
            )
            return profile
        except Exception as error:
            safe_error = error.__class__.__name__
            self.memory_store.finish_persona_refresh(
                run_id,
                status="failed",
                counts={},
                errors=[safe_error],
            )
            previous = self.memory_store.latest_persona_snapshot()
            if previous is not None:
                stale = UserProfileResponse.model_validate(previous)
                warnings = [*stale.warnings, "画像刷新失败，返回上一次快照。"]
                return stale.model_copy(
                    update={"status": PersonaStatus.STALE, "warnings": warnings}
                )
            raise PersonaError("用户画像生成失败") from error

    def _empty_profile(self) -> UserProfileResponse:
        return UserProfileResponse(
            status=PersonaStatus.EMPTY,
            generated_at=self.now(),
            reliability=PersonaReliability.LOW,
            coverage=PersonaCoverage(
                eligible_video_count=0,
                classified_video_count=0,
                classification_ratio=0,
                remaining_unclassified=0,
                source_counts={},
            ),
            warnings=["没有可用于画像的已入库收藏、稍后再看或观看历史。"],
        )

    def _seed_taxonomy(self) -> None:
        now = self.now().astimezone(timezone.utc).isoformat()
        with self.content_store.connect() as connection:
            topic_ids: dict[str, int] = {}
            for topic in TAXONOMY_TOPICS:
                if topic.parent_key is not None:
                    continue
                connection.execute(
                    """
                    INSERT INTO topics (
                        topic_key, label, description, source_type,
                        created_at, updated_at, parent_id, level,
                        taxonomy_version, is_open_tag
                    ) VALUES (?, ?, NULL, 'taxonomy', ?, ?, NULL, 1, ?, 0)
                    ON CONFLICT(topic_key) DO UPDATE SET
                        label = excluded.label,
                        updated_at = excluded.updated_at,
                        level = 1,
                        taxonomy_version = excluded.taxonomy_version,
                        is_open_tag = 0
                    """,
                    (topic.key, topic.label, now, now, TAXONOMY_VERSION),
                )
            for row in connection.execute(
                "SELECT id, topic_key FROM topics WHERE is_open_tag = 0"
            ):
                topic_ids[str(row["topic_key"])] = int(row["id"])
            for topic in TAXONOMY_TOPICS:
                if topic.parent_key is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO topics (
                        topic_key, label, description, source_type,
                        created_at, updated_at, parent_id, level,
                        taxonomy_version, is_open_tag
                    ) VALUES (?, ?, NULL, 'taxonomy', ?, ?, ?, 2, ?, 0)
                    ON CONFLICT(topic_key) DO UPDATE SET
                        label = excluded.label,
                        updated_at = excluded.updated_at,
                        parent_id = excluded.parent_id,
                        level = 2,
                        taxonomy_version = excluded.taxonomy_version,
                        is_open_tag = 0
                    """,
                    (
                        topic.key,
                        topic.label,
                        now,
                        now,
                        topic_ids[topic.parent_key],
                        TAXONOMY_VERSION,
                    ),
                )

    def _upsert_event(
        self,
        connection,
        *,
        source_ref: str,
        event_type: str,
        source: str,
        bvid: str | None,
        cid: str | None,
        weight: float,
        occurred_at: datetime,
        metadata: dict[str, object],
    ) -> None:
        now = self.now().astimezone(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO user_content_events (
                id, event_type, bvid, cid, source, weight, occurred_at,
                metadata_json, source_ref, active, derived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                event_type = excluded.event_type,
                bvid = excluded.bvid,
                cid = excluded.cid,
                source = excluded.source,
                weight = excluded.weight,
                occurred_at = excluded.occurred_at,
                metadata_json = excluded.metadata_json,
                source_ref = excluded.source_ref,
                active = 1,
                derived_at = excluded.derived_at
            """,
            (
                _event_id(source_ref),
                event_type,
                bvid,
                cid,
                source,
                weight,
                occurred_at.astimezone(timezone.utc).isoformat(),
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                source_ref,
                now,
            ),
        )

    def _derive_events(self) -> None:
        with self.content_store.connect() as connection:
            connection.execute(
                """
                UPDATE user_content_events SET active = 0
                WHERE source IN ('favorite', 'watch_later', 'history', 'search')
                """
            )
            favorite_rows = connection.execute(
                """
                SELECT fi.bvid, fi.cid,
                       MAX(COALESCE(fi.favorited_at, fi.first_seen_at)) AS event_at,
                       GROUP_CONCAT(fi.folder_id) AS folder_ids,
                       GROUP_CONCAT(ff.title) AS folder_titles
                FROM favorite_items fi
                JOIN favorite_folders ff ON ff.folder_id = fi.folder_id
                WHERE fi.removed_at IS NULL
                GROUP BY fi.bvid, fi.cid
                """
            ).fetchall()
            for row in favorite_rows:
                source_ref = f"favorite:{row['bvid']}:{row['cid']}"
                self._upsert_event(
                    connection,
                    source_ref=source_ref,
                    event_type="favorite",
                    source="favorite",
                    bvid=row["bvid"],
                    cid=row["cid"],
                    weight=5.0,
                    occurred_at=_parse_time(row["event_at"]) or self.now(),
                    metadata={
                        "folder_ids": (row["folder_ids"] or "").split(","),
                        "folder_titles": (row["folder_titles"] or "").split(","),
                    },
                )

            for row in connection.execute(
                """
                SELECT bvid, cid, first_seen_at FROM watch_later_items
                WHERE removed_at IS NULL
                """
            ).fetchall():
                source_ref = f"watch_later:{row['bvid']}:{row['cid']}"
                self._upsert_event(
                    connection,
                    source_ref=source_ref,
                    event_type="watch_later",
                    source="watch_later",
                    bvid=row["bvid"],
                    cid=row["cid"],
                    weight=2.0,
                    occurred_at=_parse_time(row["first_seen_at"]) or self.now(),
                    metadata={},
                )

            history_rows = connection.execute(
                """
                SELECT h.bvid, h.cid, h.viewed_at, h.progress_seconds,
                       v.duration_seconds
                FROM history_items h
                JOIN videos v ON v.bvid = h.bvid AND v.cid = h.cid
                """
            ).fetchall()
            for row in history_rows:
                progress = int(row["progress_seconds"])
                duration = row["duration_seconds"]
                ratio = (
                    1.0
                    if progress == -1
                    else (
                        min(1.0, max(0.0, progress / int(duration)))
                        if duration and int(duration) > 0
                        else 0.0
                    )
                )
                weight = 3.0 if ratio >= 0.9 else 1.5 if ratio >= 0.5 else 0.5 if ratio >= 0.1 else 0.0
                source_ref = (
                    f"history:{row['bvid']}:{row['cid']}:{row['viewed_at']}"
                )
                self._upsert_event(
                    connection,
                    source_ref=source_ref,
                    event_type="watch_progress",
                    source="history",
                    bvid=row["bvid"],
                    cid=row["cid"],
                    weight=weight,
                    occurred_at=_parse_time(row["viewed_at"]) or self.now(),
                    metadata={"progress_ratio": ratio},
                )

            grouped_searches: dict[tuple[str, str], datetime] = {}
            for row in connection.execute(
                "SELECT keyword, searched_at FROM search_runs"
            ).fetchall():
                keyword = _normalize_keyword(str(row["keyword"]))
                searched_at = _parse_time(row["searched_at"]) or self.now()
                if not keyword or SENSITIVE_MEMORY_PATTERN.search(keyword):
                    continue
                day = searched_at.astimezone(BEIJING_TIMEZONE).date().isoformat()
                grouped_searches[(keyword, day)] = max(
                    searched_at,
                    grouped_searches.get((keyword, day), searched_at),
                )
            for (keyword, day), searched_at in grouped_searches.items():
                source_ref = f"search:{keyword}:{day}"
                self._upsert_event(
                    connection,
                    source_ref=source_ref,
                    event_type="search",
                    source="search",
                    bvid=None,
                    cid=None,
                    weight=0.5,
                    occurred_at=searched_at,
                    metadata={"keyword": keyword},
                )

    def _eligible_videos(self) -> list[EligibleVideo]:
        with self.content_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT v.*,
                       MAX(e.occurred_at) AS latest_event_at,
                       MAX(CASE e.source
                           WHEN 'favorite' THEN 4
                           WHEN 'history' THEN CASE WHEN e.weight >= 3 THEN 3 ELSE 1 END
                           WHEN 'watch_later' THEN 2
                           ELSE 0 END) AS priority
                FROM videos v
                JOIN user_content_events e
                  ON e.bvid = v.bvid AND e.cid = v.cid AND e.active = 1
                WHERE e.source IN ('favorite', 'watch_later', 'history')
                GROUP BY v.bvid, v.cid
                """
            ).fetchall()
            folders: dict[tuple[str, str], list[str]] = defaultdict(list)
            for row in connection.execute(
                """
                SELECT fi.bvid, fi.cid, ff.title
                FROM favorite_items fi
                JOIN favorite_folders ff ON ff.folder_id = fi.folder_id
                WHERE fi.removed_at IS NULL
                """
            ).fetchall():
                title = str(row["title"])
                if not SENSITIVE_MEMORY_PATTERN.search(title):
                    folders[(row["bvid"], row["cid"])].append(title)

        result: list[EligibleVideo] = []
        for row in rows:
            title = str(row["title"] or "").strip()
            if not title:
                continue
            record = VideoRecord(
                identity=VideoIdentity(bvid=row["bvid"], cid=row["cid"]),
                author=VideoAuthor(
                    mid=row["author_mid"],
                    name=row["author_name"],
                    avatar_url=row["author_avatar_url"],
                ),
                detail=VideoDetail(
                    aid=row["aid"],
                    title=title,
                    description=row["description"],
                    cover_url=row["cover_url"],
                    duration_seconds=row["duration_seconds"],
                    published_at=_parse_time(row["published_at"]),
                    is_default_part=(
                        bool(row["is_default"])
                        if row["is_default"] is not None
                        else None
                    ),
                ),
                contexts=VideoContexts(),
                observed_at=_parse_time(row["last_seen_at"]) or self.now(),
            )
            classification_input = ClassificationInput(
                source_id=record.source_id,
                title=title[:500],
                description=(str(row["description"])[:2_000] if row["description"] else None),
                author_name=row["author_name"],
                duration_seconds=row["duration_seconds"],
                folder_titles=sorted(set(folders[(row["bvid"], row["cid"])]))[:20],
            )
            result.append(
                EligibleVideo(
                    record=record,
                    input=classification_input,
                    input_hash=_input_hash(classification_input),
                    priority=int(row["priority"]),
                    latest_event_at=_parse_time(row["latest_event_at"]) or self.now(),
                )
            )
        return sorted(
            result,
            key=lambda item: (item.priority, item.latest_event_at, item.record.source_id),
            reverse=True,
        )

    def _pending_videos(
        self, eligible: Sequence[EligibleVideo]
    ) -> list[EligibleVideo]:
        with self.content_store.connect() as connection:
            states = {
                (row["bvid"], row["cid"]): row
                for row in connection.execute(
                    "SELECT * FROM video_topic_classification_state"
                ).fetchall()
            }
        return [
            item
            for item in eligible
            if (
                (state := states.get(
                    (item.record.identity.bvid, item.record.identity.cid)
                ))
                is None
                or state["status"] != "completed"
                or state["input_hash"] != item.input_hash
                or state["classifier_version"] != CLASSIFIER_VERSION
                or state["taxonomy_version"] != TAXONOMY_VERSION
            )
        ]

    def _save_video_classifications(
        self,
        batch: Sequence[EligibleVideo],
        result: VideoTopicClassificationBatch,
    ) -> None:
        by_source = {item.record.source_id: item for item in batch}
        now = self.now().astimezone(timezone.utc).isoformat()
        with self.content_store.connect() as connection:
            topic_ids = {
                row["topic_key"]: int(row["id"])
                for row in connection.execute(
                    "SELECT id, topic_key FROM topics"
                ).fetchall()
            }
            for classification in result.results:
                item = by_source[classification.source_id]
                identity = item.record.identity
                connection.execute(
                    """
                    DELETE FROM video_topics
                    WHERE bvid = ? AND cid = ?
                      AND source_type IN ('model', 'model_tag')
                    """,
                    (identity.bvid, identity.cid),
                )
                for topic in classification.topics:
                    connection.execute(
                        """
                        INSERT INTO video_topics (
                            bvid, cid, topic_id, confidence, source_type,
                            evidence, first_seen_at, last_seen_at,
                            classifier_version, prompt_version, classified_at
                        ) VALUES (?, ?, ?, ?, 'model', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity.bvid,
                            identity.cid,
                            topic_ids[topic.topic_key],
                            topic.confidence,
                            topic.evidence,
                            now,
                            now,
                            CLASSIFIER_VERSION,
                            PROMPT_VERSION,
                            now,
                        ),
                    )
                for tag in classification.tags:
                    normalized = _normalize_keyword(tag.label)
                    if not normalized:
                        continue
                    key = "tag:" + hashlib.sha256(
                        normalized.encode("utf-8")
                    ).hexdigest()[:16]
                    connection.execute(
                        """
                        INSERT INTO topics (
                            topic_key, label, description, source_type,
                            created_at, updated_at, parent_id, level,
                            taxonomy_version, is_open_tag
                        ) VALUES (?, ?, NULL, 'model_tag', ?, ?, NULL, 2, ?, 1)
                        ON CONFLICT(topic_key) DO UPDATE SET
                            label = excluded.label, updated_at = excluded.updated_at
                        """,
                        (key, tag.label, now, now, TAXONOMY_VERSION),
                    )
                    tag_id = connection.execute(
                        "SELECT id FROM topics WHERE topic_key = ?", (key,)
                    ).fetchone()["id"]
                    connection.execute(
                        """
                        INSERT INTO video_topics (
                            bvid, cid, topic_id, confidence, source_type,
                            evidence, first_seen_at, last_seen_at,
                            classifier_version, prompt_version, classified_at
                        ) VALUES (?, ?, ?, ?, 'model_tag', NULL, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity.bvid,
                            identity.cid,
                            tag_id,
                            tag.confidence,
                            now,
                            now,
                            CLASSIFIER_VERSION,
                            PROMPT_VERSION,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO video_topic_classification_state (
                        bvid, cid, input_hash, status, model_name,
                        classifier_version, prompt_version, taxonomy_version,
                        last_attempted_at, last_succeeded_at, error_code
                    ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(bvid, cid) DO UPDATE SET
                        input_hash = excluded.input_hash,
                        status = 'completed',
                        model_name = excluded.model_name,
                        classifier_version = excluded.classifier_version,
                        prompt_version = excluded.prompt_version,
                        taxonomy_version = excluded.taxonomy_version,
                        last_attempted_at = excluded.last_attempted_at,
                        last_succeeded_at = excluded.last_succeeded_at,
                        error_code = NULL
                    """,
                    (
                        identity.bvid,
                        identity.cid,
                        item.input_hash,
                        "deepseek",
                        CLASSIFIER_VERSION,
                        PROMPT_VERSION,
                        TAXONOMY_VERSION,
                        now,
                        now,
                    ),
                )

    def _mark_classification_failures(
        self, batch: Sequence[EligibleVideo], error_code: str
    ) -> None:
        now = self.now().astimezone(timezone.utc).isoformat()
        with self.content_store.connect() as connection:
            for item in batch:
                identity = item.record.identity
                connection.execute(
                    """
                    INSERT INTO video_topic_classification_state (
                        bvid, cid, input_hash, status, model_name,
                        classifier_version, prompt_version, taxonomy_version,
                        last_attempted_at, last_succeeded_at, error_code
                    ) VALUES (?, ?, ?, 'failed', 'deepseek', ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(bvid, cid) DO UPDATE SET
                        input_hash = excluded.input_hash,
                        status = 'failed',
                        classifier_version = excluded.classifier_version,
                        prompt_version = excluded.prompt_version,
                        taxonomy_version = excluded.taxonomy_version,
                        last_attempted_at = excluded.last_attempted_at,
                        error_code = excluded.error_code
                    """,
                    (
                        identity.bvid,
                        identity.cid,
                        item.input_hash,
                        CLASSIFIER_VERSION,
                        PROMPT_VERSION,
                        TAXONOMY_VERSION,
                        now,
                        error_code,
                    ),
                )

    async def _classify_search_events(
        self,
        *,
        on_usage: Callable[[TokenUsage], None] | None,
    ) -> None:
        with self.content_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.metadata_json
                FROM user_content_events e
                WHERE e.active = 1 AND e.source = 'search'
                  AND NOT EXISTS (
                      SELECT 1 FROM event_topics et WHERE et.event_id = e.id
                  )
                ORDER BY e.occurred_at DESC LIMIT 20
                """
            ).fetchall()
        if not rows:
            return
        inputs: list[ClassificationInput] = []
        event_by_source: dict[str, str] = {}
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            keyword = str(metadata.get("keyword") or "").strip()
            if not keyword:
                continue
            source_id = f"search-query:{row['id']}"
            event_by_source[source_id] = str(row["id"])
            inputs.append(
                ClassificationInput(
                    source_id=source_id,
                    kind="search_query",
                    title=keyword,
                )
            )
        if not inputs:
            return
        result = await self.classifier(inputs, on_usage=on_usage)
        with self.content_store.connect() as connection:
            topic_ids = {
                row["topic_key"]: int(row["id"])
                for row in connection.execute(
                    "SELECT id, topic_key FROM topics WHERE is_open_tag = 0"
                ).fetchall()
            }
            for classification in result.results:
                event_id = event_by_source[classification.source_id]
                for topic in classification.topics:
                    connection.execute(
                        """
                        INSERT INTO event_topics (event_id, topic_id, confidence)
                        VALUES (?, ?, ?)
                        ON CONFLICT(event_id, topic_id) DO UPDATE SET
                            confidence = excluded.confidence
                        """,
                        (event_id, topic_ids[topic.topic_key], topic.confidence),
                    )

    def _video_weights(self) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], datetime], dict[str, int]]:
        now = self.now()
        with self.content_store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM user_content_events WHERE active = 1"
            ).fetchall()
        grouped: dict[tuple[str, str], dict[str, list[tuple[datetime, float]]]] = defaultdict(lambda: defaultdict(list))
        source_counts: dict[str, set[tuple[str, str] | str]] = defaultdict(set)
        for row in rows:
            occurred = _parse_time(row["occurred_at"]) or now
            source = str(row["source"])
            if row["bvid"] is None or row["cid"] is None:
                source_counts[source].add(str(row["id"]))
                continue
            identity = (str(row["bvid"]), str(row["cid"]))
            source_counts[source].add(identity)
            grouped[identity][source].append((occurred, float(row["weight"] or 0)))

        weights: dict[tuple[str, str], float] = {}
        latest: dict[tuple[str, str], datetime] = {}
        half_lives = {"favorite": 365.0, "watch_later": 120.0, "history": 90.0}
        for identity, sources in grouped.items():
            total = 0.0
            all_times: list[datetime] = []
            for source in ("favorite", "watch_later"):
                values = sources.get(source, [])
                if values:
                    occurred, base = max(values, key=lambda value: value[0])
                    age = (now - occurred).total_seconds() / 86_400
                    total += base * _decay(age, half_lives[source])
                    all_times.append(occurred)
            history = sorted(
                sources.get("history", []), key=lambda value: value[0], reverse=True
            )[:3]
            history_score = 0.0
            for occurred, base in history:
                age = (now - occurred).total_seconds() / 86_400
                history_score += base * _decay(age, half_lives["history"])
                all_times.append(occurred)
            total += min(6.0, history_score)
            weights[identity] = min(10.0, total)
            latest[identity] = max(all_times) if all_times else now
        return weights, latest, {key: len(value) for key, value in source_counts.items()}

    def _build_profile(
        self,
        eligible: Sequence[EligibleVideo],
        errors: Sequence[str],
    ) -> UserProfileResponse:
        now = self.now()
        weights, latest, source_counts = self._video_weights()
        with self.content_store.connect() as connection:
            classified_keys = {
                (row["bvid"], row["cid"])
                for row in connection.execute(
                    """
                    SELECT bvid, cid FROM video_topic_classification_state
                    WHERE status = 'completed'
                    """
                ).fetchall()
            }
            topic_rows = connection.execute(
                """
                SELECT vt.bvid, vt.cid, vt.confidence, t.id AS topic_id,
                       t.topic_key, t.label
                FROM video_topics vt
                JOIN topics t ON t.id = vt.topic_id
                WHERE vt.source_type = 'model' AND t.is_open_tag = 0
                """
            ).fetchall()
            search_rows = connection.execute(
                """
                SELECT e.id, e.occurred_at, e.weight, et.confidence,
                       t.id AS topic_id, t.topic_key, t.label
                FROM user_content_events e
                JOIN event_topics et ON et.event_id = e.id
                JOIN topics t ON t.id = et.topic_id
                WHERE e.active = 1 AND e.source = 'search'
                """
            ).fetchall()
            tag_rows = connection.execute(
                """
                SELECT vt.bvid, vt.cid, t.label
                FROM video_topics vt JOIN topics t ON t.id = vt.topic_id
                WHERE vt.source_type = 'model_tag' AND t.is_open_tag = 1
                """
            ).fetchall()

        eligible_keys = {
            (item.record.identity.bvid, item.record.identity.cid)
            for item in eligible
        }
        classified_count = len(eligible_keys & classified_keys)
        coverage_ratio = classified_count / len(eligible_keys) if eligible_keys else 0.0
        topic_data: dict[int, dict[str, Any]] = {}
        favorite_keys = self._favorite_keys()
        for row in topic_rows:
            key = (str(row["bvid"]), str(row["cid"]))
            behavior_weight = weights.get(key, 0.0)
            if behavior_weight <= 0:
                continue
            data = topic_data.setdefault(
                int(row["topic_id"]),
                {
                    "key": row["topic_key"],
                    "label": row["label"],
                    "raw": 0.0,
                    "quality_num": 0.0,
                    "quality_den": 0.0,
                    "videos": set(),
                    "favorite": False,
                    "search_count": 0,
                    "evidence": [],
                    "latest": latest.get(key, now),
                },
            )
            confidence = float(row["confidence"])
            contribution = behavior_weight * confidence
            data["raw"] += contribution
            data["quality_num"] += behavior_weight * confidence
            data["quality_den"] += behavior_weight
            data["videos"].add(key)
            data["favorite"] = data["favorite"] or key in favorite_keys
            data["evidence"].append((contribution, f"bilibili:video:{key[0]}:part:{key[1]}"))
            data["latest"] = max(data["latest"], latest.get(key, now))

        search_by_topic: dict[int, float] = defaultdict(float)
        for row in sorted(search_rows, key=lambda value: value["occurred_at"], reverse=True):
            occurred = _parse_time(row["occurred_at"]) or now
            age = (now - occurred).total_seconds() / 86_400
            contribution = float(row["weight"]) * _decay(age, 30.0) * float(row["confidence"])
            topic_id = int(row["topic_id"])
            available = max(0.0, 3.0 - search_by_topic[topic_id])
            accepted = min(available, contribution)
            if accepted <= 0:
                continue
            search_by_topic[topic_id] += accepted
            data = topic_data.setdefault(
                topic_id,
                {
                    "key": row["topic_key"], "label": row["label"], "raw": 0.0,
                    "quality_num": 0.0, "quality_den": 0.0, "videos": set(),
                    "favorite": False, "search_count": 0, "evidence": [],
                    "latest": occurred,
                },
            )
            data["raw"] += accepted
            data["search_count"] += 1
            data["latest"] = max(data["latest"], occurred)

        topic_preferences: list[TopicPreference] = []
        score_rows: list[tuple[Any, ...]] = []
        for topic_id, data in topic_data.items():
            distinct = len(data["videos"])
            score = _normalized_score(data["raw"])
            quality = (
                data["quality_num"] / data["quality_den"]
                if data["quality_den"]
                else 0.0
            )
            confidence = quality * min(1.0, distinct / 5.0) * (0.5 + 0.5 * coverage_ratio)
            evidence_count = distinct + int(data["search_count"])
            score_rows.append(
                (
                    topic_id, score, evidence_count, 0, ALGORITHM_VERSION,
                    now.astimezone(timezone.utc).isoformat(), data["raw"],
                    confidence, distinct, data["latest"].astimezone(timezone.utc).isoformat(),
                )
            )
            level = _preference_level(score)
            if level is None or not (distinct >= 2 or data["favorite"]):
                continue
            evidence = [
                source_id
                for _value, source_id in sorted(data["evidence"], reverse=True)[:5]
            ]
            topic_preferences.append(
                TopicPreference(
                    topic_key=data["key"], label=data["label"],
                    score=round(score, 1), confidence=round(confidence, 2),
                    level=level, evidence_count=evidence_count,
                    distinct_video_count=distinct, evidence_source_ids=evidence,
                )
            )

        creators = self._creator_preferences(weights, latest, now)
        durations, duration_status = self._duration_preferences(weights, now)
        self._save_scores(score_rows, creators, durations, now)
        tags: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in tag_rows:
            key = (str(row["bvid"]), str(row["cid"]))
            if weights.get(key, 0) > 0:
                tags[str(row["label"])].add(key)
        emerging_tags = [
            EmergingTag(label=label, evidence_count=len(video_keys))
            for label, video_keys in tags.items()
            if len(video_keys) >= 2
        ]
        emerging_tags.sort(key=lambda item: (-item.evidence_count, item.label))

        topic_preferences.sort(
            key=lambda item: (-item.score, -item.distinct_video_count, item.topic_key)
        )
        explicit = self._explicit_preferences()
        if not eligible_keys:
            reliability = PersonaReliability.LOW
            status = PersonaStatus.EMPTY
        elif len(eligible_keys) >= 50 and coverage_ratio >= 0.8:
            reliability = PersonaReliability.HIGH
            status = PersonaStatus.PARTIAL if errors else PersonaStatus.COMPLETE
        elif len(eligible_keys) < 10 or coverage_ratio < 0.5:
            reliability = PersonaReliability.LOW
            status = PersonaStatus.PARTIAL if classified_count < len(eligible_keys) or errors else PersonaStatus.COMPLETE
        else:
            reliability = PersonaReliability.MEDIUM
            status = PersonaStatus.PARTIAL if classified_count < len(eligible_keys) or errors else PersonaStatus.COMPLETE

        by_key = {
            (item.record.identity.bvid, item.record.identity.cid): item.record
            for item in eligible
        }
        evidence_records = [
            by_key[key]
            for key, _weight in sorted(
                weights.items(), key=lambda value: (value[1], latest[value[0]]), reverse=True
            )
            if key in by_key
        ][:20]
        warnings = ["画像仅基于已经通过 Agent 读取并写入数据库的内容。"]
        if errors:
            warnings.append("部分主题分类失败，已保留旧结果并使用可用证据。")
        if classified_count < len(eligible_keys):
            warnings.append("仍有视频尚未完成主题分类，可再次刷新继续处理。")
        return UserProfileResponse(
            status=status,
            generated_at=now,
            reliability=reliability,
            explicit_preferences=explicit,
            inferred=InferredProfile(
                topics=topic_preferences[:10], creators=creators[:10],
                duration_distribution=durations,
                duration_status=duration_status,
                emerging_tags=emerging_tags[:10],
            ),
            coverage=PersonaCoverage(
                eligible_video_count=len(eligible_keys),
                classified_video_count=classified_count,
                classification_ratio=round(coverage_ratio, 4),
                remaining_unclassified=max(0, len(eligible_keys) - classified_count),
                source_counts=source_counts,
            ),
            videos=evidence_records,
            warnings=warnings,
        )

    def _favorite_keys(self) -> set[tuple[str, str]]:
        with self.content_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT bvid, cid FROM user_content_events
                WHERE active = 1 AND source = 'favorite'
                """
            ).fetchall()
        return {(str(row["bvid"]), str(row["cid"])) for row in rows}

    def _creator_preferences(
        self,
        weights: dict[tuple[str, str], float],
        latest: dict[tuple[str, str], datetime],
        now: datetime,
    ) -> list[CreatorPreference]:
        if not weights:
            return []
        with self.content_store.connect() as connection:
            rows = connection.execute(
                "SELECT bvid, cid, author_mid, author_name FROM videos"
            ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = (str(row["bvid"]), str(row["cid"]))
            weight = weights.get(key, 0)
            mid = row["author_mid"]
            if weight <= 0 or mid is None:
                continue
            data = grouped.setdefault(str(mid), {"raw": 0.0, "videos": set(), "name": None, "latest": now})
            data["raw"] += weight
            data["videos"].add(key)
            if latest.get(key, now) >= data["latest"] or data["name"] is None:
                data["name"] = row["author_name"]
                data["latest"] = latest.get(key, now)
        result = [
            CreatorPreference(
                author_mid=mid, author_name=data["name"],
                score=round(_normalized_score(data["raw"]), 1),
                confidence=round(min(1.0, len(data["videos"]) / 3.0), 2),
                distinct_video_count=len(data["videos"]),
                raw_score=data["raw"],
                last_evidence_at=data["latest"],
            )
            for mid, data in grouped.items()
        ]
        return sorted(result, key=lambda item: (-item.score, item.author_mid))

    def _duration_preferences(
        self,
        weights: dict[tuple[str, str], float],
        now: datetime,
    ) -> tuple[list[DurationPreference], str]:
        del now
        with self.content_store.connect() as connection:
            rows = connection.execute(
                "SELECT bvid, cid, duration_seconds FROM videos"
            ).fetchall()
        grouped: dict[tuple[str, str], tuple[float, int]] = {}
        evidence = 0
        for row in rows:
            key = (str(row["bvid"]), str(row["cid"]))
            weight = weights.get(key, 0)
            if weight <= 0 or row["duration_seconds"] is None:
                continue
            bucket = _duration_bucket(int(row["duration_seconds"]))
            raw, count = grouped.get(bucket, (0.0, 0))
            grouped[bucket] = (raw + weight, count + 1)
            evidence += 1
        if evidence < 5:
            return [], "insufficient_data"
        total = sum(raw for raw, _count in grouped.values())
        result = [
            DurationPreference(
                key=key, label=label, ratio=round(raw / total, 4),
                evidence_count=count,
            )
            for (key, label), (raw, count) in grouped.items()
        ]
        return sorted(result, key=lambda item: (-item.ratio, item.key)), "available"

    def _explicit_preferences(self) -> list[ExplicitPreference]:
        items = self.memory_store.list_memories(states={MemoryState.ACTIVE})
        accepted = {
            MemoryKind.PREFERENCE,
            MemoryKind.CONSTRAINT,
            MemoryKind.GOAL,
        }
        return [
            ExplicitPreference(
                id=str(item.id), kind=item.kind.value, content=item.content,
                topics=item.topics, confidence=item.confidence,
            )
            for item in items
            if item.kind in accepted
        ]

    def _save_scores(
        self,
        topic_rows: Sequence[tuple[Any, ...]],
        creators: Sequence[CreatorPreference],
        durations: Sequence[DurationPreference],
        now: datetime,
    ) -> None:
        observed = now.astimezone(timezone.utc).isoformat()
        with self.content_store.connect() as connection:
            connection.execute("DELETE FROM topic_preference_scores")
            connection.executemany(
                """
                INSERT INTO topic_preference_scores (
                    topic_id, score, positive_evidence_count,
                    negative_evidence_count, algorithm_version, updated_at,
                    raw_score, confidence, distinct_video_count,
                    last_evidence_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                topic_rows,
            )
            connection.execute("DELETE FROM creator_preference_scores")
            connection.executemany(
                """
                INSERT INTO creator_preference_scores (
                    author_mid, author_name, raw_score, score, confidence,
                    distinct_video_count, last_evidence_at,
                    algorithm_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.author_mid, item.author_name, item.raw_score, item.score,
                        item.confidence, item.distinct_video_count,
                        (
                            item.last_evidence_at.astimezone(timezone.utc).isoformat()
                            if item.last_evidence_at is not None
                            else None
                        ),
                        ALGORITHM_VERSION, observed,
                    )
                    for item in creators
                ],
            )
            connection.execute("DELETE FROM profile_feature_scores")
            connection.executemany(
                """
                INSERT INTO profile_feature_scores (
                    feature_type, feature_key, label, raw_weight, ratio,
                    evidence_count, algorithm_version, updated_at
                ) VALUES ('duration', ?, ?, 0, ?, ?, ?, ?)
                """,
                [
                    (
                        item.key, item.label, item.ratio, item.evidence_count,
                        ALGORITHM_VERSION, observed,
                    )
                    for item in durations
                ],
            )
