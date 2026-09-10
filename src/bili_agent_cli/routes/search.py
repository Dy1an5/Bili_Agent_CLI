from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from bili_agent_cli.bilibili.search import SearchVideoError, search_videos
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse


router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/videos", response_model=SearchVideoResponse)
async def get_search_videos(
    query: Annotated[SearchVideoQuery, Query()],
) -> SearchVideoResponse:
    try:
        cookies = load_profile()
        return await search_videos(
            query,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except SearchVideoError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
