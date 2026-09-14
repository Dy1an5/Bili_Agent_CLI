from bili_agent_cli.bilibili.user_dynamics import fetch_user_dynamics_page
from bili_agent_cli.content.ingestion import ingest_user_dynamics
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicsArgs,
    UserDynamicsResponse,
)


async def get_user_dynamics_tool(
    args: UserDynamicsArgs,
) -> UserDynamicsResponse:
    cookies = load_profile()
    sessdata_cookie = get_sessdata_cookie_header(cookies)
    response = await fetch_user_dynamics_page(
        args.user_mid,
        args.offset,
        sessdata_cookie,
    )
    return await ingest_user_dynamics(
        response,
        sessdata_cookie=sessdata_cookie,
    )
