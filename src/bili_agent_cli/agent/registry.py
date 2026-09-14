from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pydantic import BaseModel

from bili_agent_cli.agent.models import AgentFinalAnswer
from bili_agent_cli.agent.tool_context import ToolExecutionContext
from bili_agent_cli.persona.models import GetUserProfileArgs, UserProfileResponse

from bili_agent_cli.schemas.favorites import (
    FavoriteFolderListResponse,
    FavoriteFolderVideosQuery,
    FavoriteFolderVideosResponse,
    FavoriteFoldersQuery,
    CommitFavoriteSaveArgs,
    FavoriteSavePreviewResponse,
    FavoriteSaveResponse,
    PrepareFavoriteSaveArgs,
)
from bili_agent_cli.schemas.following_feed import (
    FollowingFeedQuery,
    FollowingFeedResponse,
)
from bili_agent_cli.schemas.following_users import (
    FollowingUsersQuery,
    FollowingUsersResponse,
)
from bili_agent_cli.schemas.history import HistoryQuery, HistoryResponse
from bili_agent_cli.schemas.search import SearchVideoQuery, SearchVideoResponse
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicsArgs,
    UserDynamicsResponse,
)
from bili_agent_cli.schemas.watch_later import WatchLaterQuery, WatchLaterResponse

from .result_models import (
    project_favorite_folder_videos,
    project_favorite_folders,
    project_favorite_save,
    project_favorite_save_preview,
    project_following_feed,
    project_following_users,
    project_watch_history,
    project_search_videos,
    project_user_dynamics,
    project_watch_later,
)
from .tools.bili.get_favorites import (
    get_favorite_folder_videos_tool,
    get_favorite_folders_tool,
)
from .tools.bili.get_following_feed import get_following_feed_tool
from .tools.bili.get_following_users import get_following_users_tool
from .tools.bili.get_history import get_watch_history_tool
from .tools.bili.get_user_dynamics import get_user_dynamics_tool
from .tools.bili.get_watch_later import get_watch_later_tool
from .tools.bili.save_favorites import (
    commit_save_videos_to_favorite_folder_tool,
    prepare_save_videos_to_favorite_folder_tool,
)
from .tools.bili.search_videos import search_videos_tool
from .tools.get_user_profile import get_user_profile_tool

ToolExecutor = Callable[
    [BaseModel, ToolExecutionContext | None],
    Awaitable[BaseModel],
]
ToolResultProjector = Callable[[BaseModel], BaseModel]
SUBMIT_AGENT_ANSWER_TOOL_NAME = "submit_agent_answer"


@dataclass(frozen = True)
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    executor: ToolExecutor
    result_projector: ToolResultProjector


async def _run_get_following_feed_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, FollowingFeedQuery):
        raise TypeError("get_following_feed_tool 收到了错误的参数模型")

    return await get_following_feed_tool(args)


async def _run_get_following_users_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, FollowingUsersQuery):
        raise TypeError("get_following_users_tool 收到了错误的参数模型")

    return await get_following_users_tool(args)


async def _run_get_favorite_folders_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, FavoriteFoldersQuery):
        raise TypeError("get_favorite_folders_tool 收到了错误的参数模型")

    return await get_favorite_folders_tool(args)


async def _run_get_favorite_folder_videos_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, FavoriteFolderVideosQuery):
        raise TypeError("get_favorite_folder_videos_tool 收到了错误的参数模型")

    return await get_favorite_folder_videos_tool(args)


async def _run_get_watch_later_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, WatchLaterQuery):
        raise TypeError("get_watch_later_tool 收到了错误的参数模型")

    return await get_watch_later_tool(args)


async def _run_get_watch_history_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, HistoryQuery):
        raise TypeError("get_watch_history_tool 收到了错误的参数模型")

    return await get_watch_history_tool(args)


async def _run_search_videos_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, SearchVideoQuery):
        raise TypeError("search_videos_tool 收到了错误的参数模型")

    return await search_videos_tool(args)


async def _run_get_user_dynamics_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, UserDynamicsArgs):
        raise TypeError("get_user_dynamics_tool 收到了错误的参数模型")

    return await get_user_dynamics_tool(args)


async def _run_get_user_profile_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, GetUserProfileArgs):
        raise TypeError("get_user_profile_tool 收到了错误的参数模型")
    return await get_user_profile_tool(args, context)


async def _run_prepare_favorite_save_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, PrepareFavoriteSaveArgs):
        raise TypeError("prepare 收藏写工具收到了错误的参数模型")
    return await prepare_save_videos_to_favorite_folder_tool(args, context)


async def _run_commit_favorite_save_tool(
    args: BaseModel,
    context: ToolExecutionContext | None,
) -> BaseModel:
    if not isinstance(args, CommitFavoriteSaveArgs):
        raise TypeError("commit 收藏写工具收到了错误的参数模型")
    return await commit_save_videos_to_favorite_folder_tool(args, context)


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
        result_projector=project_following_feed,
    ),
    "get_following_users": ToolDefinition(
        name="get_following_users",
        description=(
            "分页获取当前已登录 Bilibili 账号关注的用户列表。"
            "无需前置工具，目标账号 MID 来自当前登录资料；"
            "使用 page 和 page_size 翻页，sort 可选择最近关注或最常访问。"
            "返回的 mid 可用于识别具体关注用户。"
        ),
        args_model=FollowingUsersQuery,
        result_model=FollowingUsersResponse,
        executor=_run_get_following_users_tool,
        result_projector=project_following_users,
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
        result_projector=project_favorite_folders,
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
        result_projector=project_favorite_folder_videos,
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
        result_projector=project_watch_later,
    ),
    "get_watch_history": ToolDefinition(
        name="get_watch_history",
        description=(
            "使用 max 和 view_at 双游标获取当前已登录 Bilibili 用户的"
            "普通视频观看历史。返回观看时间、播放进度和下一页游标。"
        ),
        args_model=HistoryQuery,
        result_model=HistoryResponse,
        executor=_run_get_watch_history_tool,
        result_projector=project_watch_history,
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
        result_projector=project_search_videos,
    ),
    "get_user_dynamics": ToolDefinition(
        name="get_user_dynamics",
        description=(
            "分页获取指定 Bilibili UP 主的动态。user_mid 可直接由用户提供，"
            "也可来自 get_following_users 返回的 mid；无需其他前置工具。"
            "首次调用 offset 留空；需要继续时使用返回的 next_offset。"
            "用户只要最新内容时不要继续翻页；只有用户明确要求更多或全部时才翻页。"
            "返回视频、图文、转发等动态；视频内容中的 source_id 可用于引用。"
        ),
        args_model=UserDynamicsArgs,
        result_model=UserDynamicsResponse,
        executor=_run_get_user_dynamics_tool,
        result_projector=project_user_dynamics,
    ),
    "get_user_profile": ToolDefinition(
        name="get_user_profile",
        description=(
            "基于已经写入 content.db 的收藏、稍后再看、观看历史和搜索词，"
            "结合 memory.db 中的明确长期偏好生成用户画像。"
            "用户要求分析画像、我喜欢什么、或明确要求根据个人喜好推荐时调用；"
            "普通搜索和查看动态不要调用。refresh=false 只读取最近快照。"
        ),
        args_model=GetUserProfileArgs,
        result_model=UserProfileResponse,
        executor=_run_get_user_profile_tool,
        result_projector=lambda result: result,
    ),
    "prepare_save_videos_to_favorite_folder": ToolDefinition(
        name="prepare_save_videos_to_favorite_folder",
        description=(
            "预检把此前工具获取的视频保存到指定收藏夹，不执行写入。"
            "source_ids 必须来自当前会话可信工具结果；收藏夹不存在时计划新建。"
            "返回 confirmation_id 后必须向用户展示计划，并等待新的用户轮次确认；"
            "不得在同一轮调用 commit 工具。"
        ),
        args_model=PrepareFavoriteSaveArgs,
        result_model=FavoriteSavePreviewResponse,
        executor=_run_prepare_favorite_save_tool,
        result_projector=project_favorite_save_preview,
    ),
    "commit_save_videos_to_favorite_folder": ToolDefinition(
        name="commit_save_videos_to_favorite_folder",
        description=(
            "在用户新一轮明确确认后，使用 prepare 工具返回的 confirmation_id "
            "执行收藏写入。确认 ID 绑定原计划、30 分钟过期，完整成功后不可复用；"
            "若返回 retryable=true，可在用户下一轮要求重试时继续使用同一确认 ID，"
            "工具只会重试尚未确认成功的视频。不得自行构造 ID 或修改目标。"
        ),
        args_model=CommitFavoriteSaveArgs,
        result_model=FavoriteSaveResponse,
        executor=_run_commit_favorite_save_tool,
        result_projector=project_favorite_save,
    ),
}


def build_tool_schemas() -> list[dict[str, object]]:
    tools = [
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
    tools.append(
        {
            "type": "function",
            "function": {
                "name": SUBMIT_AGENT_ANSWER_TOOL_NAME,
                "description": (
                    "提交最终回答。完成数据工具调用后必须单独调用此工具；"
                    "source_ids 只能填写工具结果中真实存在且回答实际使用的 source_id。"
                ),
                "parameters": AgentFinalAnswer.model_json_schema(),
            },
        }
    )
    return tools
