from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from bili_agent_cli.bilibili.following_users import (
    FollowingUsersError,
    fetch_following_users,
)
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    get_user_id,
    load_profile,
)
from bili_agent_cli.schemas.following_users import (
    FollowingUsersQuery,
    FollowingUsersResponse,
)


router = APIRouter(prefix="/api/following", tags=["following"])


@router.get("/users", response_model=FollowingUsersResponse)
async def get_following_users(
    query: Annotated[FollowingUsersQuery, Query()],
) -> FollowingUsersResponse:
    try:
        cookies = load_profile()
        return await fetch_following_users(
            get_user_id(cookies),
            query,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except FollowingUsersError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
