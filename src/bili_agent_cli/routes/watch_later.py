from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from bili_agent_cli.bilibili.watch_later import WatchLaterError, fetch_watch_later
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.watch_later import WatchLaterQuery, WatchLaterResponse


router = APIRouter(prefix="/api/watch-later", tags=["watch-later"])


@router.get("", response_model=WatchLaterResponse)
async def get_watch_later(
    query: Annotated[WatchLaterQuery, Query()],
) -> WatchLaterResponse:
    try:
        cookies = load_profile()
        return await fetch_watch_later(
            query,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except WatchLaterError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
