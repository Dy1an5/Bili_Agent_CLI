from pydantic import ValidationError

from bili_agent_cli.agent.tool_context import ToolExecutionContext
from bili_agent_cli.agent.tool_errors import (
    FavoriteConfirmationError,
    FavoriteConfirmationSameTurnError,
    UntrustedVideoSourcesError,
)
from bili_agent_cli.bilibili.favorites import FavoriteWriteError
from bili_agent_cli.bilibili.following_users import FollowingUsersError
from bili_agent_cli.bilibili.user_dynamics import UserDynamicsError
from bili_agent_cli.bilibili.subtitles import VideoSubtitleError
from bili_agent_cli.profile import ProfileError
from bili_agent_cli.persona.service import PersonaError
from bili_agent_cli.content.subtitles import VideoSubtitleStorageError
from .registry import TOOL_REGISTRY

async def execute_tool(
    tool_name: str,
    raw_arguments: object,
    context: ToolExecutionContext | None = None,
) -> dict[str, object]:
    definition = TOOL_REGISTRY.get(tool_name)

    if definition is None:
        return {
            "ok": False,
            "error": "UNKNOWN_TOOL",
        }

    try:
        args = definition.args_model.model_validate(raw_arguments)

    except ValidationError:
        return {
            "ok": False,
            "error": "INVALID_TOOL_ARGUMENTS",
        }

    try:
        raw_result = await definition.executor(args, context)

    except ProfileError:
        return {
            "ok": False,
            "error": "AUTH_PROFILE_ERROR",
        }

    except PersonaError:
        return {
            "ok": False,
            "error": "USER_PROFILE_ERROR",
        }

    except FollowingUsersError:
        return {
            "ok": False,
            "error": "FOLLOWING_USERS_FETCH_ERROR",
        }

    except UserDynamicsError as error:
        return {
            "ok": False,
            "error": "USER_DYNAMICS_FETCH_ERROR",
            "details": {
                "upstream_code": error.code,
                "upstream_message": str(error),
                "http_status": error.status,
            },
        }

    except VideoSubtitleError as error:
        return {
            "ok": False,
            "error": "VIDEO_SUBTITLE_FETCH_ERROR",
            "details": {
                "upstream_code": error.code,
                "http_status": error.status,
            },
        }

    except VideoSubtitleStorageError:
        return {
            "ok": False,
            "error": "VIDEO_SUBTITLE_STORAGE_ERROR",
        }

    except UntrustedVideoSourcesError:
        return {
            "ok": False,
            "error": "UNTRUSTED_VIDEO_SOURCES",
        }

    except FavoriteConfirmationSameTurnError:
        return {
            "ok": False,
            "error": "FAVORITE_CONFIRMATION_REQUIRES_NEW_TURN",
        }

    except FavoriteConfirmationError:
        return {
            "ok": False,
            "error": "INVALID_OR_EXPIRED_FAVORITE_CONFIRMATION",
        }

    except FavoriteWriteError as error:
        return {
            "ok": False,
            "error": "FAVORITE_SAVE_ERROR",
            "details": {
                "upstream_code": error.code,
                "upstream_message": str(error),
                "http_status": error.status,
                "outcome_unknown": error.outcome_unknown,
            },
        }

    except Exception:
        return {
            "ok": False,
            "error": "TOOL_EXECUTION_ERROR",
        }

    try:
        result = definition.result_model.model_validate(raw_result)

    except ValidationError:
        return {
            "ok": False,
            "error": "INVALID_TOOL_RESULT"
        }

    try:
        llm_result = definition.result_projector(result)

    except ValidationError:
        return {
            "ok": False,
            "error": "INVALID_LLM_TOOL_RESULT",
        }

    return {
        "ok": True,
        "data": llm_result.model_dump(mode="json"),
    }
