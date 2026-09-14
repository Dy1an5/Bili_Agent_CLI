from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    as_record,
    normalize_image_url,
    read_non_empty_string,
    read_optional_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.bilibili.wbi import (
    WbiSigningError,
    fetch_wbi_mixin_key,
    sign_wbi_parameters,
)
from bili_agent_cli.schemas.user_dynamics import (
    UserDynamicAuthor,
    UserDynamicContent,
    UserDynamicItem,
    UserDynamicsResponse,
    UserDynamicStats,
)


USER_DYNAMICS_URL = (
    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
)
USER_DYNAMICS_ORIGIN = "https://space.bilibili.com"
USER_DYNAMICS_FEATURES = "itemOpusStyle,listOnlyfans,onlyfansQaCard"
_MAJOR_PAYLOAD_KEYS = (
    "archive",
    "opus",
    "ugc_season",
    "pgc",
    "courses",
    "live_rcmd",
    "live",
    "common",
    "upower_common",
    "music",
    "medialist",
    "subscription_new",
    "none",
    "blocked",
)


class UserDynamicsError(Exception):
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


def _build_parameters(user_mid: str, offset: str) -> dict[str, Any]:
    return {
        "host_mid": user_mid,
        "offset": offset,
        "timezone_offset": "-480",
        "features": USER_DYNAMICS_FEATURES,
        "platform": "web",
        "web_location": "333.1387",
        "dm_img_list": "[]",
        "dm_img_str": _random_device_value(16, 64),
        "dm_cover_img_str": _random_device_value(32, 128),
        "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
        "x-bili-device-req-json": (
            '{"platform":"web","device":"pc","spmid":"333.1387"}'
        ),
    }


def _random_device_value(min_length: int, max_length: int) -> str:
    length = min_length + secrets.randbelow(max_length - min_length + 1)
    random_bytes = bytes(0x26 + secrets.randbelow(0x59) for _ in range(length))
    return base64.b64encode(random_bytes).decode("ascii").rstrip("=")


def _read_optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _read_count(module_stat: dict[str, Any] | None, key: str) -> int | None:
    stat = as_record(module_stat.get(key)) if module_stat is not None else None
    return (
        read_optional_non_negative_int(stat.get("count"))
        if stat is not None
        else None
    )


def _read_datetime(value: Any) -> datetime | None:
    timestamp = read_positive_timestamp(value)
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if timestamp is not None
        else None
    )


def _read_image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    urls: list[str] = []
    for raw_picture in value:
        picture = as_record(raw_picture)
        if picture is None:
            continue
        url = normalize_image_url(picture.get("url")) or normalize_image_url(
            picture.get("src")
        )
        if url is not None and url not in urls:
            urls.append(url)
    return urls


def _read_live_content(value: Any) -> dict[str, Any] | None:
    live = as_record(value)
    if live is None:
        return None

    content = live.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return live

    parsed_content = as_record(content)
    if parsed_content is None:
        return live

    return as_record(parsed_content.get("live_play_info")) or parsed_content


def _major_payload(major: dict[str, Any]) -> dict[str, Any] | None:
    for key in _MAJOR_PAYLOAD_KEYS:
        payload = as_record(major.get(key))
        if payload is not None:
            if key == "live_rcmd":
                return _read_live_content(payload)
            return payload
    return None


def _parse_content(modules: dict[str, Any]) -> UserDynamicContent:
    dynamic = as_record(modules.get("module_dynamic"))
    description = as_record(dynamic.get("desc")) if dynamic is not None else None
    major = as_record(dynamic.get("major")) if dynamic is not None else None
    payload = _major_payload(major) if major is not None else None
    summary = as_record(payload.get("summary")) if payload is not None else None

    text = (
        read_non_empty_string(description.get("text"))
        if description is not None
        else None
    )
    if text is None and summary is not None:
        text = read_non_empty_string(summary.get("text"))

    image_urls = _read_image_urls(payload.get("pics")) if payload else []
    cover_url = normalize_image_url(payload.get("cover")) if payload else None

    return UserDynamicContent(
        major_type=(
            read_non_empty_string(major.get("type"))
            if major is not None
            else None
        ),
        title=(
            read_non_empty_string(payload.get("title"))
            if payload is not None
            else None
        ),
        text=text,
        bvid=(
            read_non_empty_string(payload.get("bvid"))
            if payload is not None
            else None
        ),
        jump_url=(
            normalize_image_url(payload.get("jump_url"))
            if payload is not None
            else None
        ),
        cover_url=cover_url,
        image_urls=image_urls,
    )


def _parse_dynamic(value: Any, *, include_original: bool = True) -> UserDynamicItem | None:
    item = as_record(value)
    if item is None:
        return None

    dynamic_id = read_positive_id(item.get("id_str"))
    modules = as_record(item.get("modules"))
    if dynamic_id is None or modules is None:
        return None

    author = as_record(modules.get("module_author"))
    module_stat = as_record(modules.get("module_stat"))
    parsed_author = None
    published_at = None
    is_pinned = None
    if author is not None:
        author_mid = read_positive_id(author.get("mid"))
        author_name = read_non_empty_string(author.get("name"))
        avatar_url = normalize_image_url(author.get("face"))
        if any((author_mid, author_name, avatar_url)):
            parsed_author = UserDynamicAuthor(
                mid=author_mid,
                name=author_name,
                avatar_url=avatar_url,
            )
        published_at = _read_datetime(author.get("pub_ts"))
        is_pinned = _read_optional_bool(author.get("is_top"))

    original = None
    if include_original and item.get("orig") is not None:
        original = _parse_dynamic(item.get("orig"), include_original=False)

    return UserDynamicItem(
        dynamic_id=dynamic_id,
        type=read_non_empty_string(item.get("type")),
        published_at=published_at,
        visible=_read_optional_bool(item.get("visible")),
        is_pinned=is_pinned,
        author=parsed_author,
        content=_parse_content(modules),
        stats=UserDynamicStats(
            likes=_read_count(module_stat, "like"),
            replies=_read_count(module_stat, "comment"),
            reposts=_read_count(module_stat, "forward"),
            favorites=_read_count(module_stat, "favorite"),
        ),
        original=original,
    )


async def _request_user_dynamics_page(
    client: httpx.AsyncClient,
    user_mid: str,
    offset: str,
    mixin_key: str,
    sessdata_cookie: str,
) -> dict[str, Any]:
    parameters = sign_wbi_parameters(
        _build_parameters(user_mid, offset),
        mixin_key,
    )
    try:
        response = await client.get(
            USER_DYNAMICS_URL,
            params=parameters,
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Origin": USER_DYNAMICS_ORIGIN,
                "Referer": f"{USER_DYNAMICS_ORIGIN}/{user_mid}/dynamic",
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise UserDynamicsError(
            f"UP主动态接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise UserDynamicsError("UP主动态接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise UserDynamicsError("UP主动态接口返回了无法解析的 JSON") from error

    if payload is None:
        raise UserDynamicsError("UP主动态接口返回格式不正确")

    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise UserDynamicsError("UP主动态接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise UserDynamicsError(
            f"UP主动态接口错误 {code}: {message}",
            code=code,
        )

    return payload


def _parse_page(payload: dict[str, Any]) -> tuple[list[Any], bool, str | None]:
    data = as_record(payload.get("data"))
    raw_items = data.get("items") if data is not None else None
    has_more = data.get("has_more") if data is not None else None
    if not isinstance(raw_items, list) or not isinstance(has_more, bool):
        raise UserDynamicsError("UP主动态接口成功响应格式不正确")

    offset = read_non_empty_string(data.get("offset"))
    if has_more and offset is None:
        raise UserDynamicsError("UP主动态接口分页游标缺失")
    return raw_items, has_more, offset


async def fetch_user_dynamics_page(
    user_mid: str,
    offset: str,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> UserDynamicsResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        try:
            mixin_key = await fetch_wbi_mixin_key(
                request_client,
                sessdata_cookie,
            )
        except WbiSigningError as error:
            raise UserDynamicsError(
                str(error),
                status=error.status,
                code=error.code,
            ) from error

        payload = await _request_user_dynamics_page(
            request_client,
            user_mid,
            offset,
            mixin_key,
            sessdata_cookie,
        )
        raw_items, has_more, next_offset = _parse_page(payload)
    finally:
        if owns_client:
            await request_client.aclose()

    items: list[UserDynamicItem] = []
    seen_dynamic_ids: set[str] = set()
    skipped_count = 0
    for value in raw_items:
        parsed = _parse_dynamic(value)
        if parsed is None:
            skipped_count += 1
        elif parsed.dynamic_id not in seen_dynamic_ids:
            seen_dynamic_ids.add(parsed.dynamic_id)
            items.append(parsed)

    return UserDynamicsResponse(
        user_mid=user_mid,
        items=items,
        total_count=len(items),
        skipped_count=skipped_count,
        pages_fetched=1,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )


async def fetch_all_user_dynamics(
    user_mid: str,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> UserDynamicsResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        try:
            mixin_key = await fetch_wbi_mixin_key(
                request_client,
                sessdata_cookie,
            )
        except WbiSigningError as error:
            raise UserDynamicsError(
                str(error),
                status=error.status,
                code=error.code,
            ) from error

        items: list[UserDynamicItem] = []
        seen_dynamic_ids: set[str] = set()
        seen_offsets: set[str] = set()
        skipped_count = 0
        pages_fetched = 0
        offset = ""

        while True:
            payload = await _request_user_dynamics_page(
                request_client,
                user_mid,
                offset,
                mixin_key,
                sessdata_cookie,
            )
            pages_fetched += 1
            raw_items, has_more, next_offset = _parse_page(payload)

            for value in raw_items:
                parsed = _parse_dynamic(value)
                if parsed is None:
                    skipped_count += 1
                elif parsed.dynamic_id not in seen_dynamic_ids:
                    seen_dynamic_ids.add(parsed.dynamic_id)
                    items.append(parsed)

            if not has_more:
                break

            if next_offset in seen_offsets or next_offset == offset:
                raise UserDynamicsError("UP主动态接口分页游标重复")

            seen_offsets.add(next_offset)
            offset = next_offset
    finally:
        if owns_client:
            await request_client.aclose()

    return UserDynamicsResponse(
        user_mid=user_mid,
        items=items,
        total_count=len(items),
        skipped_count=skipped_count,
        pages_fetched=pages_fetched,
    )
