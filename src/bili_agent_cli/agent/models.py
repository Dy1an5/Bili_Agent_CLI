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

    task: str = Field(
        min_length = 1,
        max_length = 1000
    )

class AgentSource(BaseModel):
    bvid: str
    cid :str

class AgentStep(BaseModel):
    step: int
    tool: str
    ok: bool

class AgentRunResponse(BaseModel):
    answer: str
    sources: list[AgentSource] = Field(
        default_factory=list
    )
    trace: list[AgentStep] = Field(
        default_factory=list
    )

