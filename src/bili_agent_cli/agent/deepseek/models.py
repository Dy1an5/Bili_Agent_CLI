from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
)

from bili_agent_cli.agent.models import TokenUsage

NonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2000,
    ),
]

class ChatRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    message: NonEmptyString


class ChatResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    answer: NonEmptyString
    model: NonEmptyString
    usage: TokenUsage
