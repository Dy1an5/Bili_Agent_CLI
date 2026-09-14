from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bili_agent_cli.agent.context.models import ConversationSession
from bili_agent_cli.agent.models import AgentSource


@dataclass(frozen=True)
class ToolExecutionContext:
    session: ConversationSession
    current_turn_id: UUID
    trusted_sources: list[AgentSource]
