from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.agent.models import AgentPaginationState, AgentSource


class AgentEvidenceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] | None
    result: dict[str, Any]


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    status: Literal["pending", "completed", "interrupted"] = "completed"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    user_content: str
    assistant_content: str = ""
    error_code: str | None = None
    sources: list[AgentSource] = Field(default_factory=list)
    evidence_batches: list[AgentEvidenceBatch] = Field(default_factory=list)
    pagination_states: list[AgentPaginationState] = Field(default_factory=list)


class ConversationSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: UUID
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    pending_turn: ConversationTurn | None = None


@dataclass
class ConversationSession:
    id: UUID
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    turns: list[ConversationTurn] = field(default_factory=list)
    pending_turn: ConversationTurn | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def to_record(self) -> ConversationSessionRecord:
        return ConversationSessionRecord(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            summary=self.summary,
            turns=self.turns,
            pending_turn=self.pending_turn,
        )

    @classmethod
    def from_record(
        cls,
        record: ConversationSessionRecord,
    ) -> ConversationSession:
        return cls(
            id=record.id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            summary=record.summary,
            turns=record.turns,
            pending_turn=record.pending_turn,
        )
