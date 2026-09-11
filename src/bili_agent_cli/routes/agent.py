from fastapi import APIRouter

from bili_agent_cli.agent.agent_loop import run_agent
from bili_agent_cli.agent.models import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/run", response_model=AgentRunResponse)
async def agent_run(request: AgentRunRequest) -> AgentRunResponse:
    return await run_agent(request.task, request.session_id)
