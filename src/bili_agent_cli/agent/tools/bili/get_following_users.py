from bili_agent_cli.bilibili.following_users import fetch_following_users
from bili_agent_cli.profile import (
    get_sessdata_cookie_header,
    get_user_id,
    load_profile,
)
from bili_agent_cli.schemas.following_users import (
    FollowingUsersQuery,
    FollowingUsersResponse,
)


async def get_following_users_tool(
    args: FollowingUsersQuery,
) -> FollowingUsersResponse:
    cookies = load_profile()
    return await fetch_following_users(
        get_user_id(cookies),
        args,
        get_sessdata_cookie_header(cookies),
    )
