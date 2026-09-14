from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from bili_agent_cli.bilibili.subtitles import (
    fetch_video_subtitle,
    select_subtitle_track,
)
from bili_agent_cli.schemas.subtitles import (
    GetVideoSubtitleArgs,
    SubtitleStatus,
    VideoSubtitleDocument,
    VideoSubtitleResponse,
)

from .store import ContentStorageError, ContentStore


SUBTITLE_CACHE_TTL = timedelta(hours=24)
subtitle_store = ContentStore()


class VideoSubtitleStorageError(Exception):
    pass


def _select_cached_document(
    documents: list[VideoSubtitleDocument],
    language: str | None,
) -> VideoSubtitleDocument | None:
    if not documents:
        return None
    newest = max(documents, key=lambda document: document.fetched_at)
    selected_track = select_subtitle_track(newest.available_tracks, language)
    if selected_track is None:
        return None
    return next(
        (
            document
            for document in documents
            if document.track == selected_track
        ),
        None,
    )


def _page_document(
    document: VideoSubtitleDocument,
    args: GetVideoSubtitleArgs,
    *,
    cached: bool,
) -> VideoSubtitleResponse:
    cues = document.cues[args.offset : args.offset + args.limit]
    has_more = args.offset + len(cues) < len(document.cues)
    return VideoSubtitleResponse(
        status=SubtitleStatus.AVAILABLE,
        bvid=document.bvid,
        cid=document.cid,
        track=document.track,
        available_tracks=document.available_tracks,
        cues=cues,
        source_hash=document.source_hash,
        total_cues=len(document.cues),
        offset=args.offset,
        limit=args.limit,
        has_more=has_more,
        next_offset=args.offset + len(cues) if has_more else None,
        fetched_at=document.fetched_at,
        cached=cached,
    )


async def get_video_subtitle(
    args: GetVideoSubtitleArgs,
    sessdata_cookie: str,
    *,
    client: httpx.AsyncClient | None = None,
    store: ContentStore | None = None,
    now: datetime | None = None,
) -> VideoSubtitleResponse:
    store = store or subtitle_store
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    if not args.refresh:
        try:
            cached_documents = store.load_subtitle_documents(
                args.bvid,
                args.cid,
                fetched_after=current_time - SUBTITLE_CACHE_TTL,
            )
        except ContentStorageError as error:
            raise VideoSubtitleStorageError("无法读取字幕缓存") from error
        cached_document = _select_cached_document(
            cached_documents,
            args.language,
        )
        if cached_document is not None:
            return _page_document(cached_document, args, cached=True)

    fetched = await fetch_video_subtitle(
        args.bvid,
        args.cid,
        sessdata_cookie,
        language=args.language,
        client=client,
    )
    if fetched.document is None:
        return VideoSubtitleResponse(
            status=SubtitleStatus.UNAVAILABLE,
            bvid=args.bvid,
            cid=args.cid,
            available_tracks=list(fetched.available_tracks),
            total_cues=0,
            offset=args.offset,
            limit=args.limit,
            has_more=False,
            next_offset=None,
            fetched_at=fetched.fetched_at,
            cached=False,
            reason=fetched.reason,
        )

    try:
        store.save_subtitle_document(fetched.document)
    except ContentStorageError as error:
        raise VideoSubtitleStorageError("无法保存字幕缓存") from error
    return _page_document(fetched.document, args, cached=False)
