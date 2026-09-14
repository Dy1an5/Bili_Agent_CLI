from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bili_agent_cli.agent.context.models import PendingFavoriteSave
from bili_agent_cli.agent.tool_context import ToolExecutionContext
from bili_agent_cli.agent.tool_errors import (
    FavoriteConfirmationError,
    FavoriteConfirmationSameTurnError,
    UntrustedVideoSourcesError,
)
from bili_agent_cli.bilibili.favorites import (
    execute_favorite_save,
    prepare_favorite_save,
)
from bili_agent_cli.profile import (
    get_csrf_token,
    get_sessdata_cookie_header,
    get_user_id,
    get_write_cookie_header,
    load_profile,
)
from bili_agent_cli.schemas.favorites import (
    CommitFavoriteSaveArgs,
    FavoriteSavePreviewResponse,
    FavoriteSaveResponse,
    FavoriteSaveStatus,
    PrepareFavoriteSaveArgs,
    SaveVideosToFavoriteFolderRequest,
)


FAVORITE_CONFIRMATION_TTL = timedelta(minutes=30)


def _require_context(
    context: ToolExecutionContext | None,
) -> ToolExecutionContext:
    if context is None:
        raise FavoriteConfirmationError("收藏写工具缺少会话上下文")
    return context


async def prepare_save_videos_to_favorite_folder_tool(
    args: PrepareFavoriteSaveArgs,
    context: ToolExecutionContext | None,
) -> FavoriteSavePreviewResponse:
    execution_context = _require_context(context)
    trusted_by_id = {
        source.source_id: source
        for source in execution_context.trusted_sources
    }
    if any(source_id not in trusted_by_id for source_id in args.source_ids):
        raise UntrustedVideoSourcesError

    request = SaveVideosToFavoriteFolderRequest(
        folder_title=args.folder_title,
        bvids=[trusted_by_id[source_id].bvid for source_id in args.source_ids],
        privacy=args.privacy,
    )
    cookies = load_profile()
    plan = await prepare_favorite_save(
        request,
        get_user_id(cookies),
        get_sessdata_cookie_header(cookies),
    )
    now = datetime.now(timezone.utc)
    confirmation_id = uuid4()
    expires_at = now + FAVORITE_CONFIRMATION_TTL
    execution_context.session.pending_favorite_save = PendingFavoriteSave(
        confirmation_id=confirmation_id,
        prepared_turn_id=execution_context.current_turn_id,
        created_at=now,
        expires_at=expires_at,
        plan=plan,
    )
    return FavoriteSavePreviewResponse(
        confirmation_id=confirmation_id,
        expires_at=expires_at,
        **plan.model_dump(mode="python"),
    )


async def commit_save_videos_to_favorite_folder_tool(
    args: CommitFavoriteSaveArgs,
    context: ToolExecutionContext | None,
) -> FavoriteSaveResponse:
    execution_context = _require_context(context)
    pending = execution_context.session.pending_favorite_save
    now = datetime.now(timezone.utc)

    if (
        pending is None
        or pending.confirmation_id != args.confirmation_id
        or pending.expires_at <= now
    ):
        if pending is not None and pending.expires_at <= now:
            execution_context.session.pending_favorite_save = None
        raise FavoriteConfirmationError

    if pending.prepared_turn_id == execution_context.current_turn_id:
        raise FavoriteConfirmationSameTurnError

    cookies = load_profile()
    result = await execute_favorite_save(
        pending.plan,
        get_user_id(cookies),
        get_sessdata_cookie_header(cookies),
        get_write_cookie_header(cookies),
        get_csrf_token(cookies),
    )
    if result.status is FavoriteSaveStatus.COMPLETED:
        execution_context.session.pending_favorite_save = None
    else:
        pending.plan = pending.plan.model_copy(
            update={
                "existing_folder_id": result.folder.id,
                "will_create_folder": False,
                "videos": result.retry_videos or pending.plan.videos,
            }
        )
    return result
