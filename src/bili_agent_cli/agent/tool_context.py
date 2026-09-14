from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from uuid import UUID

from bili_agent_cli.agent.context.models import ConversationSession
from bili_agent_cli.agent.models import AgentSource
from bili_agent_cli.agent.models import TokenUsage


@dataclass(frozen=True)
class ToolExecutionContext:
    session: ConversationSession
    current_turn_id: UUID
    trusted_sources: list[AgentSource]
    on_usage: Callable[[TokenUsage], None] | None = None
