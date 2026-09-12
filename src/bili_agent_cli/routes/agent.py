from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from bili_agent_cli.agent.agent_loop import run_agent, session_store
from bili_agent_cli.agent.context import (
    ContextBudgetExceededError,
    SessionNotFoundError,
    SessionStorageError,
)
from bili_agent_cli.agent.models import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/run", response_model=AgentRunResponse)
async def agent_run(request: AgentRunRequest) -> AgentRunResponse:
    try:
        return await run_agent(request.task, request.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND"},
        ) from exc
    except ContextBudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "CONTEXT_BUDGET_EXCEEDED"},
        ) from exc
    except SessionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SESSION_STORAGE_ERROR"},
        ) from exc


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent_session(session_id: UUID) -> Response:
    try:
        await session_store.delete(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND"},
        ) from exc
    except SessionStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SESSION_STORAGE_ERROR"},
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
