from bili_agent_cli.bilibili.history import fetch_watch_history
from bili_agent_cli.content.ingestion import ingest_history
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.history import HistoryQuery, HistoryResponse


async def get_watch_history_tool(args: HistoryQuery) -> HistoryResponse:
    cookies = load_profile()
    sessdata_cookie = get_sessdata_cookie_header(cookies)
    response = await fetch_watch_history(
        args,
        sessdata_cookie,
    )
    return await ingest_history(
        response,
        sessdata_cookie=sessdata_cookie,
    )
