from bili_agent_cli.bilibili.following import (
    FollowingFeedError,
    fetch_following_feed,
)
from bili_agent_cli.profile import (
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.following import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)

async def get_following_feed_tool(
    args: FollowingFeedQuery,
) -> FollowingFeedResponse:
    cookies = load_profile()

    sessdata_cookie = get_sessdata_cookie_header(cookies)

    return await fetch_following_feed(args, sessdata_cookie)