from __future__ import annotations

from pydantic import BaseModel, Field


class VideoStats(BaseModel):
    views: int | None = Field(default=None, ge=0)
    danmaku: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
