from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field, computed_field

from bili_agent_cli.bilibili.video_details import (
    VideoDetailError,
    fetch_default_video_record,
)

from .models import VideoRecord


class CidModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CidCandidate(CidModel):
    key: str = Field(min_length=1)
    bvid: str = Field(min_length=1)
    cid: str | None = Field(default=None, pattern=r"^[1-9]\d*$")


class ResolvedCid(CidModel):
    key: str
    bvid: str
    cid: str
    is_default: bool | None
    detail_record: VideoRecord | None = None


class CidResolutionFailure(CidModel):
    key: str
    bvid: str
    reason: str
    upstream_status: int | None = None
    upstream_code: int | None = None


class CidResolutionBatch(CidModel):
    resolved: list[ResolvedCid]
    failures: list[CidResolutionFailure]

    @computed_field
    @property
    def status(self) -> str:
        return "partial" if self.failures else "completed"

    @computed_field
    @property
    def skipped_count(self) -> int:
        return len(self.failures)


DefaultCidLookup = Callable[[str], str | None]
FetchDefaultVideo = Callable[
    [str, str, httpx.AsyncClient | None], Awaitable[VideoRecord]
]


async def resolve_video_cids(
    candidates: Sequence[CidCandidate],
    *,
    sessdata_cookie: str,
    lookup_default_cid: DefaultCidLookup,
    client: httpx.AsyncClient | None = None,
    concurrency: int = 5,
    fetch_default_video: FetchDefaultVideo = fetch_default_video_record,
) -> CidResolutionBatch:
    resolved_by_key: dict[str, ResolvedCid] = {}
    failures_by_key: dict[str, CidResolutionFailure] = {}
    pending_by_bvid: dict[str, list[CidCandidate]] = {}

    for candidate in candidates:
        if candidate.cid is not None:
            resolved_by_key[candidate.key] = ResolvedCid(
                key=candidate.key,
                bvid=candidate.bvid,
                cid=candidate.cid,
                is_default=None,
            )
            continue
        cached_cid = lookup_default_cid(candidate.bvid)
        if cached_cid is not None:
            resolved_by_key[candidate.key] = ResolvedCid(
                key=candidate.key,
                bvid=candidate.bvid,
                cid=cached_cid,
                is_default=True,
            )
            continue
        pending_by_bvid.setdefault(candidate.bvid, []).append(candidate)

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def fetch_one(bvid: str) -> tuple[str, VideoRecord | VideoDetailError]:
        async with semaphore:
            try:
                return bvid, await fetch_default_video(
                    bvid, sessdata_cookie, client
                )
            except VideoDetailError as error:
                return bvid, error

    fetched = await asyncio.gather(
        *(fetch_one(bvid) for bvid in pending_by_bvid)
    )
    for bvid, result in fetched:
        related = pending_by_bvid[bvid]
        if isinstance(result, VideoDetailError):
            for candidate in related:
                failures_by_key[candidate.key] = CidResolutionFailure(
                    key=candidate.key,
                    bvid=bvid,
                    reason=str(result),
                    upstream_status=result.status,
                    upstream_code=result.code,
                )
            continue
        for candidate in related:
            resolved_by_key[candidate.key] = ResolvedCid(
                key=candidate.key,
                bvid=bvid,
                cid=result.identity.cid,
                is_default=True,
                detail_record=result,
            )

    return CidResolutionBatch(
        resolved=[
            resolved_by_key[candidate.key]
            for candidate in candidates
            if candidate.key in resolved_by_key
        ],
        failures=[
            failures_by_key[candidate.key]
            for candidate in candidates
            if candidate.key in failures_by_key
        ],
    )
