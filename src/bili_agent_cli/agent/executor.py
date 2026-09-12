from pydantic import ValidationError

from bili_agent_cli.schemas.following import FollowingNotFoundError
from .registry import TOOL_REGISTRY

async def execute_tool(
    tool_name: str,
    raw_arguments: object
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
        raw_result = await definition.executor(args)

    except FollowingNotFoundError:
        return {
            "ok": False,
            "error": "FOLLOWING NOT FOUND",
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
