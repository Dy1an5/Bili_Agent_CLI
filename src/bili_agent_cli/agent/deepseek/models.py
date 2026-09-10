from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

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

class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

class ChatResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    answer: NonEmptyString
    model: NonEmptyString
    usage: TokenUsage