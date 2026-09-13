from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"

class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    kind: MemoryKind    
    content: str = Field(min_length=1, max_length=500)
    topics: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
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

class MemoryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=10)

class MemoryItem(MemoryCandidate):
    id: UUID = Field(default_factory=uuid4)
    source_type: MemorySourceType
    source_ref: str | None = None
    state: MemoryState = MemoryState.ACTIVE
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
