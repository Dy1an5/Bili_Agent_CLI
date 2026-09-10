from bili_agent_cli.bilibili.watch_later import fetch_watch_later
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.watch_later import WatchLaterQuery, WatchLaterResponse


async def get_watch_later_tool(
    args: WatchLaterQuery,
) -> WatchLaterResponse:
    cookies = load_profile()
    return await fetch_watch_later(
        args,
        get_sessdata_cookie_header(cookies),
    )
