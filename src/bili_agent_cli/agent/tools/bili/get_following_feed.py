from bili_agent_cli.bilibili.following_feed import fetch_following_feed
from bili_agent_cli.content.ingestion import ingest_following_feed
from bili_agent_cli.profile import (
    get_sessdata_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)

async def get_following_feed_tool(
    args: FollowingFeedQuery,
) -> FollowingFeedResponse:
    cookies = load_profile()

    sessdata_cookie = get_sessdata_cookie_header(cookies)

    response = await fetch_following_feed(args, sessdata_cookie)
    return await ingest_following_feed(
        response,
        sessdata_cookie=sessdata_cookie,
    )
