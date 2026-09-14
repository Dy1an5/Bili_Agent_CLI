from bili_agent_cli.bilibili.user_dynamics import fetch_all_user_dynamics
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicsArgs,
    UserDynamicsResponse,
)


async def get_user_dynamics_tool(
    args: UserDynamicsArgs,
) -> UserDynamicsResponse:
    cookies = load_profile()
    return await fetch_all_user_dynamics(
        args.user_mid,
        get_sessdata_cookie_header(cookies),
    )
