from bili_agent_cli.bilibili.search import search_videos
from bili_agent_cli.content.ingestion import ingest_search
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse


async def search_videos_tool(
    args: SearchVideoQuery,
) -> SearchVideoResponse:
    cookies = load_profile()
    sessdata_cookie = get_sessdata_cookie_header(cookies)
    response = await search_videos(
        args,
        sessdata_cookie,
    )
    return await ingest_search(
        response,
        args,
        sessdata_cookie=sessdata_cookie,
    )
