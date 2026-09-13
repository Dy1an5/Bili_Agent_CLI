from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    GOAL = "goal"
    FACT = "fact"


class MemorySourceType(StrEnum):
    USER = "user"
    CONVERSATION = "conversation"
    BILIBILI = "bilibili"


class MemoryState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    EXPIRED = "expired"


class MemoryScope(StrEnum):
    GLOBAL = "global"
    INTENT = "intent"
    TOPIC = "topic"


class MemoryDurability(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=500)
    topics: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=500)
    durability: MemoryDurability
    scope: MemoryScope
    scope_value: str | None = Field(max_length=100)

    @field_validator("content", "evidence_quote")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("topics")
    @classmethod
    def clean_topics(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            topic = value.strip().lower()
            if topic and topic not in result:
                result.append(topic)
        return result

    @field_validator("scope_value")
    @classmethod
    def clean_scope_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @model_validator(mode="after")
    def validate_scope_value(self) -> MemoryCandidate:
        if self.scope == MemoryScope.GLOBAL and self.scope_value is not None:
            raise ValueError("global memory must not define scope_value")
        if self.scope != MemoryScope.GLOBAL and self.scope_value is None:
            raise ValueError("non-global memory requires scope_value")
        return self


class MemoryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=10)


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    key: str
    kind: MemoryKind
    content: str
    topics: list[str]
    confidence: float
    durability: MemoryDurability
    scope: MemoryScope
    scope_value: str | None = None
    source_type: MemorySourceType
    source_ref: str | None = None
    state: MemoryState
    evidence_count: int = Field(ge=0)
    first_observed_at: datetime
    last_observed_at: datetime
    activated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_injected_at: datetime | None = None


class MemoryEvidence(BaseModel):
    id: int
    memory_id: UUID
    source_ref: str
    user_excerpt: str
    created_at: datetime


class MemoryObservationAction(StrEnum):
    CREATED_ACTIVE = "created_active"
    CREATED_PENDING = "created_pending"
    UPDATED_ACTIVE = "updated_active"
    UPDATED_PENDING = "updated_pending"
    PROMOTED = "promoted"
    SUPERSEDED = "superseded"


class MemoryObservationResult(BaseModel):
    item: MemoryItem
    action: MemoryObservationAction
