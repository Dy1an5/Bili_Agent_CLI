from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

class AgentRunRequest(BaseModel):
    model_config = ConfigDict(
        strict = True,
        extra = "forbid"
    )

    task: str = Field(min_length = 1, max_length = 1000)
    session_id: UUID | None = None

class AgentSource(BaseModel):
    bvid: str
    cid :str | None = None
    title: str | None = None
    author_name: str | None = None
    folder_id: str | None = None

class AgentStep(BaseModel):
    step: int
    tool: str
    ok: bool

class AgentRunResponse(BaseModel):
    answer: str
    session_id: UUID
    context_compacted: bool = False
    sources: list[AgentSource] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)

