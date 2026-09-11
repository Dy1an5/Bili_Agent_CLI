from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bili_agent_cli.agent.models import AgentSource

class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_content: str
    assistant_content: str
    sources: list[AgentSource] = Field(default_factory=list)

@dataclass
class ConversationSession:
    id: UUID
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    turns: list[ConversationTurn] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
