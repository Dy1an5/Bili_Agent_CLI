from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from bili_agent_cli.bilibili.following_feed import (
    FollowingFeedError,
    fetch_following_feed,
)
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)


router = APIRouter(prefix="/api/following", tags=["following"])


@router.get("/feed", response_model=FollowingFeedResponse)
async def get_following_feed(
    query: Annotated[FollowingFeedQuery, Query()],
) -> FollowingFeedResponse:
    try:
        cookies = load_profile()
        sessdata_cookie = get_sessdata_cookie_header(cookies)
        return await fetch_following_feed(query, sessdata_cookie)
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except FollowingFeedError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
