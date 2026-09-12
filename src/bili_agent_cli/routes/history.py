from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from bili_agent_cli.bilibili.history import HistoryError, fetch_watch_history
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.history import HistoryQuery, HistoryResponse


router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=HistoryResponse)
async def get_watch_history(
    query: Annotated[HistoryQuery, Query()],
) -> HistoryResponse:
    try:
        cookies = load_profile()
        return await fetch_watch_history(
            query,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except HistoryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
