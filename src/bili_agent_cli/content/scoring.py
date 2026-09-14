from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .models import VideoIdentity


class ScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicSource(StrEnum):
    CODE = "code"
    MODEL = "model"
    USER = "user"
    UPSTREAM = "upstream"


class TopicCandidate(ScoringModel):
    topic_key: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    source: TopicSource
    evidence: str | None = None


class ContentEventType(StrEnum):
    IMPRESSION = "impression"
    OPEN = "open"
    WATCH_PROGRESS = "watch_progress"
    LIKE = "like"
    FAVORITE = "favorite"
    WATCH_LATER = "watch_later"
    DISMISS = "dismiss"


class UserContentEvent(ScoringModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: ContentEventType
    video: VideoIdentity | None = None
    source: str = Field(min_length=1, max_length=100)
    weight: float | None = None
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ScoreComponent(ScoringModel):
    name: str = Field(min_length=1, max_length=100)
    value: float
    reason: str | None = None


class VideoScoreResult(ScoringModel):
    video: VideoIdentity
    score: float
    components: list[ScoreComponent] = Field(default_factory=list)
    algorithm_version: str = Field(min_length=1, max_length=100)
    calculated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
