from bili_agent_cli.bilibili.favorites import (
    fetch_favorite_folder_videos,
    fetch_favorite_folders,
)
from bili_agent_cli.profile import (
    get_sessdata_cookie_header,
    get_user_id,
    load_profile,
)
from bili_agent_cli.schemas.favorites import (
    FavoriteFolderListResponse,
    FavoriteFolderVideosQuery,
    FavoriteFolderVideosResponse,
    FavoriteFoldersQuery,
    FavoriteVideosQuery,
)


async def get_favorite_folders_tool(
    args: FavoriteFoldersQuery,
) -> FavoriteFolderListResponse:
    cookies = load_profile()
    return await fetch_favorite_folders(
        get_user_id(cookies),
        get_sessdata_cookie_header(cookies),
    )


async def get_favorite_folder_videos_tool(
    args: FavoriteFolderVideosQuery,
) -> FavoriteFolderVideosResponse:
    cookies = load_profile()
    query = FavoriteVideosQuery(
        page=args.page,
        page_size=args.page_size,
    )
    return await fetch_favorite_folder_videos(
        args.folder_id,
        query,
        get_sessdata_cookie_header(cookies),
    )
