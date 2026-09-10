from bili_agent_cli.bilibili.search import search_videos
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse


async def search_videos_tool(
    args: SearchVideoQuery,
) -> SearchVideoResponse:
    cookies = load_profile()
    return await search_videos(
        args,
        get_sessdata_cookie_header(cookies),
    )
