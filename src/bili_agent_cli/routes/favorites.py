from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from bili_agent_cli.bilibili.favorites import (
    FavoritesError,
    fetch_favorite_folder_videos,
    fetch_favorite_folders,
)
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    get_user_id,
    load_profile,
)
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderListResponse,
    FavoriteFolderVideosResponse,
    FavoriteVideosQuery,
)


router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("/folders", response_model=FavoriteFolderListResponse)
async def get_favorite_folders() -> FavoriteFolderListResponse:
    try:
        cookies = load_profile()
        return await fetch_favorite_folders(
            get_user_id(cookies),
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except FavoritesError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get(
    "/folders/{folder_id}/videos",
    response_model=FavoriteFolderVideosResponse,
)
async def get_favorite_folder_videos(
    folder_id: Annotated[int, Path(gt=0)],
    query: Annotated[FavoriteVideosQuery, Query()],
) -> FavoriteFolderVideosResponse:
    try:
        cookies = load_profile()
        return await fetch_favorite_folder_videos(
            str(folder_id),
            query,
            get_sessdata_cookie_header(cookies),
        )
    except ProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except FavoritesError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
