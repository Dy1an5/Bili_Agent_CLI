from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FollowingUsersSort(StrEnum):
    RECENT = "recent"
    FREQUENT = "frequent"


class FollowingUsersQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(
        default=1,
        ge=1,
        description="页码，从 1 开始。",
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=50,
        description="每页用户数，范围 1 到 50。",
    )
    sort: FollowingUsersSort = Field(
        default=FollowingUsersSort.RECENT,
        description="排序方式：recent 最近关注，frequent 最常访问。",
    )


class FollowingOfficialVerification(BaseModel):
    type: int | None = None
    description: str | None = None


class FollowingUser(BaseModel):
    mid: str
    name: str
    avatar_url: str | None = None
    signature: str | None = None
    followed_at: datetime | None = None
    is_mutual: bool | None = None
    is_special: bool | None = None
    official_verification: FollowingOfficialVerification | None = None


class FollowingUsersResponse(BaseModel):
    users: list[FollowingUser]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    has_more: bool
