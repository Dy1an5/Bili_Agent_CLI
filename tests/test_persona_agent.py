from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bili_agent_cli.agent.executor import execute_tool
from bili_agent_cli.persona.models import (
    PersonaCoverage,
    PersonaReliability,
    PersonaStatus,
    UserProfileResponse,
)
from bili_agent_cli.persona.service import PersonaError


def empty_profile() -> UserProfileResponse:
    return UserProfileResponse(
        status=PersonaStatus.EMPTY,
        generated_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        reliability=PersonaReliability.LOW,
        coverage=PersonaCoverage(
            eligible_video_count=0,
            classified_video_count=0,
            classification_ratio=0,
            remaining_unclassified=0,
        ),
    )


def test_agent_executes_user_profile_tool() -> None:
    with patch(
        "bili_agent_cli.agent.tools.get_user_profile.persona_service.get_profile",
        new=AsyncMock(return_value=empty_profile()),
    ) as profile_mock:
        result = asyncio.run(
            execute_tool(
                "get_user_profile",
                {"refresh": False, "max_new_videos": 25},
            )
        )

    assert result["ok"] is True
    assert result["data"]["status"] == "empty"
    args = profile_mock.await_args.args[0]
    assert args.refresh is False
    assert args.max_new_videos == 25


def test_agent_maps_persona_failure_to_stable_error() -> None:
    with patch(
        "bili_agent_cli.agent.tools.get_user_profile.persona_service.get_profile",
        new=AsyncMock(side_effect=PersonaError("failed")),
    ):
        result = asyncio.run(execute_tool("get_user_profile", {}))

    assert result == {"ok": False, "error": "USER_PROFILE_ERROR"}


def test_agent_rejects_invalid_profile_batch_limit() -> None:
    result = asyncio.run(
        execute_tool("get_user_profile", {"max_new_videos": 201})
    )

    assert result == {"ok": False, "error": "INVALID_TOOL_ARGUMENTS"}
