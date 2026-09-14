from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bili_agent_cli.content.models import VideoRecord
from bili_agent_cli.content.time import BeijingTime


class PersonaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetUserProfileArgs(PersonaModel):
    refresh: bool = True
    max_new_videos: int = Field(default=60, ge=1, le=200)


class PersonaStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    STALE = "stale"
    EMPTY = "empty"


class PersonaReliability(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PreferenceLevel(StrEnum):
    STRONG = "strong"
    MEDIUM = "medium"
    EXPLORATORY = "exploratory"


class ClassifiedTopic(PersonaModel):
    topic_key: str
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=300)


class OpenTag(PersonaModel):
    label: str = Field(min_length=1, max_length=50)
    confidence: float = Field(ge=0, le=1)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return value.strip()


class VideoTopicClassification(PersonaModel):
    source_id: str
    topics: list[ClassifiedTopic] = Field(min_length=1, max_length=3)
    tags: list[OpenTag] = Field(default_factory=list, max_length=3)


class VideoTopicClassificationBatch(PersonaModel):
    results: list[VideoTopicClassification] = Field(max_length=20)


class ExplicitPreference(PersonaModel):
    id: str
    kind: str
    content: str
    topics: list[str]
    confidence: float


class TopicPreference(PersonaModel):
    topic_key: str
    label: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    level: PreferenceLevel
    evidence_count: int = Field(ge=1)
    distinct_video_count: int = Field(ge=0)
    evidence_source_ids: list[str] = Field(default_factory=list)


class CreatorPreference(PersonaModel):
    author_mid: str
    author_name: str | None
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    distinct_video_count: int = Field(ge=1)
    raw_score: float = Field(default=0, ge=0, exclude=True)
    last_evidence_at: BeijingTime | None = Field(default=None, exclude=True)


class DurationPreference(PersonaModel):
    key: str
    label: str
    ratio: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)


class EmergingTag(PersonaModel):
    label: str
    evidence_count: int = Field(ge=2)


class InferredProfile(PersonaModel):
    topics: list[TopicPreference] = Field(default_factory=list)
    creators: list[CreatorPreference] = Field(default_factory=list)
    duration_distribution: list[DurationPreference] = Field(default_factory=list)
    duration_status: str = "insufficient_data"
    emerging_tags: list[EmergingTag] = Field(default_factory=list)


class PersonaCoverage(PersonaModel):
    eligible_video_count: int = Field(ge=0)
    classified_video_count: int = Field(ge=0)
    classification_ratio: float = Field(ge=0, le=1)
    remaining_unclassified: int = Field(ge=0)
    source_counts: dict[str, int] = Field(default_factory=dict)


class UserProfileResponse(PersonaModel):
    schema_version: str = "persona.v1"
    status: PersonaStatus
    snapshot_version: int | None = Field(default=None, ge=1)
    generated_at: BeijingTime
    reliability: PersonaReliability
    explicit_preferences: list[ExplicitPreference] = Field(default_factory=list)
    inferred: InferredProfile = Field(default_factory=InferredProfile)
    coverage: PersonaCoverage
    videos: list[VideoRecord] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list)
