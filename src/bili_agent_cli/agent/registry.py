from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pydantic import BaseModel

from bili_agent_cli.schemas.favorites import (
    FavoriteFolderListResponse,
    FavoriteFolderVideosQuery,
    FavoriteFolderVideosResponse,
    FavoriteFoldersQuery,
)
from bili_agent_cli.schemas.following import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)
from bili_agent_cli.schemas.watch_later import WatchLaterQuery, WatchLaterResponse
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse

from .tools.bili.get_favorites import (
    get_favorite_folder_videos_tool,
    get_favorite_folders_tool,
)
from .tools.bili.get_following import get_following_feed_tool
from .tools.bili.get_watch_later import get_watch_later_tool
from .tools.bili.search_videos import search_videos_tool

ToolExecutor = Callable[[BaseModel], Awaitable[BaseModel]]

@dataclass(frozen = True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    executor: ToolExecutor

async def _run_get_following_feed_tool(args: BaseModel) -> BaseModel:
    if not isinstance(args, FollowingFeedQuery):
        raise TypeError("get_following_feed_tool 收到了错误的参数模型")

    return await get_following_feed_tool(args)


async def _run_get_favorite_folders_tool(args: BaseModel) -> BaseModel:
    if not isinstance(args, FavoriteFoldersQuery):
        raise TypeError("get_favorite_folders_tool 收到了错误的参数模型")

    return await get_favorite_folders_tool(args)


async def _run_get_favorite_folder_videos_tool(args: BaseModel) -> BaseModel:
    if not isinstance(args, FavoriteFolderVideosQuery):
        raise TypeError("get_favorite_folder_videos_tool 收到了错误的参数模型")

    return await get_favorite_folder_videos_tool(args)


async def _run_get_watch_later_tool(args: BaseModel) -> BaseModel:
    if not isinstance(args, WatchLaterQuery):
        raise TypeError("get_watch_later_tool 收到了错误的参数模型")

    return await get_watch_later_tool(args)


async def _run_search_videos_tool(args: BaseModel) -> BaseModel:
    if not isinstance(args, SearchVideoQuery):
        raise TypeError("search_videos_tool 收到了错误的参数模型")

    return await search_videos_tool(args)

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "get_following_feed": ToolDefinition(
        name = "get_following_feed",
        description = (
            "获取当前已登录 Bilibili 用户的关注动态。"
            "可以用于查看最近关注 UP 主发布的视频和动态。"
        ),
        args_model = FollowingFeedQuery,
        result_model = FollowingFeedResponse,
        executor = _run_get_following_feed_tool,
    ),
    "get_favorite_folders": ToolDefinition(
        name="get_favorite_folders",
        description=(
            "列出当前已登录 Bilibili 用户创建的收藏夹，只返回收藏夹信息。"
            "当用户用名称指定收藏夹时，应先调用此工具取得 folder_id，"
            "再调用 get_favorite_folder_videos。"
        ),
        args_model=FavoriteFoldersQuery,
        result_model=FavoriteFolderListResponse,
        executor=_run_get_favorite_folders_tool,
    ),
    "get_favorite_folder_videos": ToolDefinition(
        name="get_favorite_folder_videos",
        description=(
            "根据 folder_id 分页获取一个收藏夹中的视频。"
            "folder_id 应来自 get_favorite_folders 的 id 字段；"
            "收藏夹名称不能直接作为 folder_id。"
        ),
        args_model=FavoriteFolderVideosQuery,
        result_model=FavoriteFolderVideosResponse,
        executor=_run_get_favorite_folder_videos_tool,
    ),
    "get_watch_later": ToolDefinition(
        name="get_watch_later",
        description=(
            "分页获取当前已登录 Bilibili 用户的稍后再看视频。"
            "可指定正序或倒序，返回视频总数、播放进度和发布时间。"
        ),
        args_model=WatchLaterQuery,
        result_model=WatchLaterResponse,
        executor=_run_get_watch_later_tool,
    ),
    "search_videos": ToolDefinition(
        name="search_videos",
        description=(
            "按关键词搜索 Bilibili 视频，并支持分页、排序、时长、"
            "内容分区和发布时间范围筛选。"
        ),
        args_model=SearchVideoQuery,
        result_model=SearchVideoResponse,
        executor=_run_search_videos_tool,
    ),
}

def build_tool_schemas() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.args_model.model_json_schema(),
            },
        }
        for definition in TOOL_REGISTRY.values()
    ]
