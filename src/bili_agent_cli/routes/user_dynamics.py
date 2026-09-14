from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from bili_agent_cli.bilibili.user_dynamics import (
    UserDynamicsError,
    fetch_all_user_dynamics,
)
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.user_dynamics import UserDynamicsResponse


router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_mid}/dynamics", response_model=UserDynamicsResponse)
async def get_all_user_dynamics(
    user_mid: Annotated[str, Path(pattern=r"^[1-9]\d*$")],
) -> UserDynamicsResponse:
    try:
        cookies = load_profile()
        return await fetch_all_user_dynamics(
            user_mid,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except UserDynamicsError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
