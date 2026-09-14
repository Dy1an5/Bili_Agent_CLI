from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import as_record, read_non_empty_string
from bili_agent_cli.bilibili.wbi import (
    WbiSigningError,
    fetch_wbi_mixin_key,
    sign_wbi_parameters,
)
from bili_agent_cli.schemas.subtitles import (
    SubtitleCue,
    SubtitleTrack,
    SubtitleTrackSource,
    SubtitleUnavailableReason,
    VideoSubtitleDocument,
)


PLAYER_INFO_URL = "https://api.bilibili.com/x/player/wbi/v2"


class VideoSubtitleError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class SubtitleFetchResult:
    document: VideoSubtitleDocument | None
    available_tracks: tuple[SubtitleTrack, ...]
    reason: SubtitleUnavailableReason | None
    fetched_at: datetime


@dataclass(frozen=True)
class _SubtitleTrackResource:
    track: SubtitleTrack
    url: str


def subtitle_track_priority(track: SubtitleTrack) -> tuple[int, str]:
    is_chinese = "zh" in track.language.casefold()
    is_ai = track.source is SubtitleTrackSource.AI
    if is_chinese and not is_ai:
        rank = 0
    elif is_chinese:
        rank = 1
    elif not is_ai:
        rank = 2
    else:
        rank = 3
    return rank, track.language.casefold()


def select_subtitle_track(
    tracks: list[SubtitleTrack],
    language: str | None,
) -> SubtitleTrack | None:
    candidates = tracks
    if language is not None:
        expected = language.casefold()
        candidates = [
            track for track in candidates if track.language.casefold() == expected
        ]
    return min(candidates, key=subtitle_track_priority, default=None)


def _normalize_subtitle_url(value: Any) -> str | None:
    url = read_non_empty_string(value)
    if url is None:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return url


def _parse_track(value: Any) -> _SubtitleTrackResource | None:
    record = as_record(value)
    if record is None:
        return None
    language = read_non_empty_string(record.get("lan"))
    url = _normalize_subtitle_url(record.get("subtitle_url"))
    if url is None:
        url = _normalize_subtitle_url(record.get("subtitle_url_v2"))
    if language is None or url is None:
        return None
    display_name = read_non_empty_string(record.get("lan_doc")) or language
    raw_type = record.get("type")
    source = (
        SubtitleTrackSource.AI
        if (
            (raw_type == 1 and not isinstance(raw_type, bool))
            or raw_type == "1"
        )
        else SubtitleTrackSource.HUMAN
    )
    return _SubtitleTrackResource(
        track=SubtitleTrack(
            language=language,
            display_name=display_name,
            source=source,
        ),
        url=url,
    )


def _parse_tracks(values: list[Any]) -> list[_SubtitleTrackResource]:
    tracks: list[_SubtitleTrackResource] = []
    seen: set[tuple[str, SubtitleTrackSource]] = set()
    for value in values:
        resource = _parse_track(value)
        if resource is None:
            continue
        key = (resource.track.language.casefold(), resource.track.source)
        if key in seen:
            continue
        seen.add(key)
        tracks.append(resource)
    return sorted(tracks, key=lambda resource: subtitle_track_priority(resource.track))


def _read_finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_cues(payload: Any) -> list[SubtitleCue]:
    document = as_record(payload)
    if document is None:
        raise VideoSubtitleError("字幕文件返回格式不正确")
    raw_cues = document.get("body")
    if not isinstance(raw_cues, list):
        raise VideoSubtitleError("字幕文件缺少 body 列表")

    cues: list[SubtitleCue] = []
    for raw_cue in raw_cues:
        cue = as_record(raw_cue)
        if cue is None:
            continue
        start_seconds = _read_finite_number(cue.get("from"))
        end_seconds = _read_finite_number(cue.get("to"))
        text = read_non_empty_string(cue.get("content"))
        if text is not None:
            text = re.sub(r"\s+", " ", text).strip()
        if (
            start_seconds is None
            or end_seconds is None
            or start_seconds < 0
            or end_seconds <= start_seconds
            or not text
        ):
            continue
        cues.append(
            SubtitleCue(
                index=len(cues),
                start_ms=round(start_seconds * 1000),
                end_ms=round(end_seconds * 1000),
                text=text,
            )
        )
    if not cues:
        raise VideoSubtitleError("字幕文件中没有可用文本")
    return cues


def _source_hash(cues: list[SubtitleCue]) -> str:
    digest = hashlib.sha256()
    for cue in cues:
        digest.update(
            f"{cue.start_ms}:{cue.end_ms}:{cue.text}\n".encode("utf-8")
        )
    return f"sha256:{digest.hexdigest()}"


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    resource_name: str,
) -> Any:
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise VideoSubtitleError(
            f"{resource_name}请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise VideoSubtitleError(f"{resource_name}网络请求失败") from error
    try:
        return response.json()
    except ValueError as error:
        raise VideoSubtitleError(f"{resource_name}返回了无法解析的 JSON") from error


async def fetch_video_subtitle(
    bvid: str,
    cid: str,
    sessdata_cookie: str,
    *,
    language: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> SubtitleFetchResult:
    """读取一个 UGC 视频分 P 的首选或指定语言字幕。"""

    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)
    try:
        try:
            mixin_key = await fetch_wbi_mixin_key(request_client, sessdata_cookie)
        except WbiSigningError as error:
            raise VideoSubtitleError(
                str(error),
                status=error.status,
                code=error.code,
            ) from error

        parameters = sign_wbi_parameters(
            {"bvid": bvid, "cid": cid},
            mixin_key,
        )
        payload = await _request_json(
            request_client,
            PLAYER_INFO_URL,
            params=parameters,
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
            resource_name="播放器信息",
        )
        player_info = as_record(payload)
        if player_info is None:
            raise VideoSubtitleError("播放器信息返回格式不正确")
        code = player_info.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise VideoSubtitleError("播放器信息缺少业务状态码")
        if code != 0:
            message = read_non_empty_string(player_info.get("message")) or "未知错误"
            raise VideoSubtitleError(
                f"播放器信息接口错误 {code}: {message}",
                code=code,
            )
        data = as_record(player_info.get("data"))
        if data is None:
            raise VideoSubtitleError("播放器信息缺少 data")

        raw_subtitle = data.get("subtitle")
        if raw_subtitle is None:
            raw_tracks: list[Any] = []
        else:
            subtitle = as_record(raw_subtitle)
            if subtitle is None:
                raise VideoSubtitleError("播放器字幕信息返回格式不正确")
            raw_tracks_value = subtitle.get("subtitles")
            if raw_tracks_value is None:
                raw_tracks = []
            elif isinstance(raw_tracks_value, list):
                raw_tracks = raw_tracks_value
            else:
                raise VideoSubtitleError("播放器字幕轨道列表格式不正确")

        resources = _parse_tracks(raw_tracks)
        available_tracks = [resource.track for resource in resources]
        selected_track = select_subtitle_track(available_tracks, language)
        fetched_at = datetime.now(timezone.utc)
        if selected_track is None:
            reason = (
                SubtitleUnavailableReason.LANGUAGE_NOT_FOUND
                if language is not None and available_tracks
                else SubtitleUnavailableReason.NO_SUBTITLE
            )
            return SubtitleFetchResult(
                document=None,
                available_tracks=tuple(available_tracks),
                reason=reason,
                fetched_at=fetched_at,
            )

        selected_resource = next(
            resource for resource in resources if resource.track == selected_track
        )
        subtitle_payload = await _request_json(
            request_client,
            selected_resource.url,
            headers={
                "Accept": "application/json",
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
            resource_name="字幕文件",
        )
        cues = _parse_cues(subtitle_payload)
        return SubtitleFetchResult(
            document=VideoSubtitleDocument(
                bvid=bvid,
                cid=cid,
                track=selected_track,
                available_tracks=available_tracks,
                cues=cues,
                source_hash=_source_hash(cues),
                fetched_at=fetched_at,
            ),
            available_tracks=tuple(available_tracks),
            reason=None,
            fetched_at=fetched_at,
        )
    finally:
        if owns_client:
            await request_client.aclose()
