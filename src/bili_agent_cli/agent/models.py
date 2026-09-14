from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def build_video_source_id(bvid: str) -> str:
    return f"bilibili:video:{bvid}"


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(
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
    source_id: str = ""
    source_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_source_id(self) -> AgentSource:
        expected_source_id = build_video_source_id(self.bvid)
        if self.source_id and self.source_id != expected_source_id:
            raise ValueError("source_id 与 bvid 不匹配")
        self.source_id = expected_source_id
        return self


class AgentFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    source_ids: list[str] = Field(max_length=200)

    @field_validator("answer")
    @classmethod
    def strip_answer(cls, value: str) -> str:
        answer = value.strip()
        if not answer:
            raise ValueError("answer 不能为空")
        return answer

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            source_id = value.strip()
            if not source_id:
                raise ValueError("source_id 不能为空")
            if source_id not in result:
                result.append(source_id)
        return result


class AgentPaginationState(BaseModel):
    tool: str
    has_more: bool
    next_arguments: dict[str, str | int | float | bool | None] | None = None


class AgentStep(BaseModel):
    step: int
    tool: str
    ok: bool


class AgentMemoryStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    SAVED = "saved"
    PENDING = "pending"
    MIXED = "mixed"
    NO_CANDIDATES = "no_candidates"
    FILTERED = "filtered"
    EXTRACTION_FAILED = "extraction_failed"
    STORAGE_FAILED = "storage_failed"


class AgentMemoryResult(BaseModel):
    status: AgentMemoryStatus = AgentMemoryStatus.NOT_ATTEMPTED
    saved_count: int = Field(default=0, ge=0)
    extracted_count: int = Field(default=0, ge=0)
    active_saved_count: int = Field(default=0, ge=0)
    pending_saved_count: int = Field(default=0, ge=0)
    promoted_count: int = Field(default=0, ge=0)
    updated_count: int = Field(default=0, ge=0)
    filtered_count: int = Field(default=0, ge=0)
    error_code: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class AgentRunResponse(BaseModel):
    answer: str
    session_id: UUID
    context_compacted: bool = False
    sources: list[AgentSource] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)
    memory: AgentMemoryResult = Field(default_factory=AgentMemoryResult)
    usage: TokenUsage = Field(
        default_factory=lambda: TokenUsage(
            input_tokens=0,
            output_tokens=0,
        ),
        exclude=True,
    )
