from __future__ import annotations

from bili_agent_cli.agent.tool_context import ToolExecutionContext
from bili_agent_cli.persona.models import GetUserProfileArgs, UserProfileResponse
from bili_agent_cli.persona.service import PersonaService


persona_service = PersonaService()


async def get_user_profile_tool(
    args: GetUserProfileArgs,
    context: ToolExecutionContext | None = None,
) -> UserProfileResponse:
    return await persona_service.get_profile(
        args,
        on_usage=context.on_usage if context is not None else None,
    )
