from bili_agent_cli.content.subtitles import get_video_subtitle
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile
from bili_agent_cli.schemas.subtitles import (
    GetVideoSubtitleArgs,
    VideoSubtitleResponse,
)


async def get_video_subtitle_tool(
    args: GetVideoSubtitleArgs,
) -> VideoSubtitleResponse:
    cookies = load_profile()
    return await get_video_subtitle(
        args,
        get_sessdata_cookie_header(cookies),
    )
