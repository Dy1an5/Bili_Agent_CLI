from __future__ import annotations

from typing import Any

import httpx

from bili_agent_cli.bilibili.auth import BILIBILI_REFERER, BILIBILI_USER_AGENT
from bili_agent_cli.bilibili.parsing import (
    as_record,
    normalize_image_url,
    read_non_empty_string,
    read_optional_non_negative_int,
    read_positive_id,
    read_positive_timestamp,
)
from bili_agent_cli.schemas.following_users import (
    FollowingOfficialVerification,
    FollowingUser,
    FollowingUsersQuery,
    FollowingUsersResponse,
    FollowingUsersSort,
)


FOLLOWING_USERS_URL = "https://api.bilibili.com/x/relation/followings"


class FollowingUsersError(Exception):
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


def _following_users_parameters(
    user_mid: str,
    query: FollowingUsersQuery,
) -> dict[str, str | int]:
    order_type = (
        "attention" if query.sort is FollowingUsersSort.FREQUENT else ""
    )
    return {
        "vmid": user_mid,
        "pn": query.page,
        "ps": query.page_size,
        "order": "desc",
        "order_type": order_type,
    }


def _read_optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None

    return None


def _read_binary_flag(value: Any) -> bool | None:
    parsed = _read_optional_int(value)
    return bool(parsed) if parsed in {0, 1} else None


def _parse_official_verification(
    value: Any,
) -> FollowingOfficialVerification | None:
    verification = as_record(value)

    if verification is None:
        return None

    verification_type = _read_optional_int(verification.get("type"))
    description = read_non_empty_string(verification.get("desc"))

    if verification_type is None and description is None:
        return None

    return FollowingOfficialVerification(
        type=verification_type,
        description=description,
    )


def _parse_following_user(value: Any) -> FollowingUser | None:
    user = as_record(value)

    if user is None:
        return None

    mid = read_positive_id(user.get("mid"))
    name = read_non_empty_string(user.get("uname"))

    if mid is None or name is None:
        return None

    attribute = read_optional_non_negative_int(user.get("attribute"))
    is_mutual = None if attribute is None else attribute == 6

    return FollowingUser(
        mid=mid,
        name=name,
        avatar_url=normalize_image_url(user.get("face")),
        signature=read_non_empty_string(user.get("sign")),
        followed_at=read_positive_timestamp(user.get("mtime")),
        is_mutual=is_mutual,
        is_special=_read_binary_flag(user.get("special")),
        official_verification=_parse_official_verification(
            user.get("official_verify")
        ),
    )


async def _request_following_users(
    client: httpx.AsyncClient,
    user_mid: str,
    query: FollowingUsersQuery,
    sessdata_cookie: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            FOLLOWING_USERS_URL,
            params=_following_users_parameters(user_mid, query),
            headers={
                "Accept": "application/json",
                "Cookie": sessdata_cookie,
                "Referer": BILIBILI_REFERER,
                "User-Agent": BILIBILI_USER_AGENT,
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise FollowingUsersError(
            f"关注列表接口请求失败，HTTP {error.response.status_code}",
            status=error.response.status_code,
        ) from error
    except httpx.RequestError as error:
        raise FollowingUsersError("关注列表接口网络请求失败") from error

    try:
        payload = as_record(response.json())
    except ValueError as error:
        raise FollowingUsersError("关注列表接口返回了无法解析的 JSON") from error

    if payload is None:
        raise FollowingUsersError("关注列表接口返回格式不正确")

    code = payload.get("code")

    if not isinstance(code, int) or isinstance(code, bool):
        raise FollowingUsersError("关注列表接口返回格式不正确，缺少业务状态码")

    if code != 0:
        message = read_non_empty_string(payload.get("message")) or "未知错误"
        raise FollowingUsersError(
            f"关注列表接口错误 {code}: {message}",
            code=code,
        )

    return payload


async def fetch_following_users(
    user_mid: str,
    query: FollowingUsersQuery,
    sessdata_cookie: str,
    client: httpx.AsyncClient | None = None,
) -> FollowingUsersResponse:
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=20.0)

    try:
        payload = await _request_following_users(
            request_client,
            user_mid,
            query,
            sessdata_cookie,
        )
    finally:
        if owns_client:
            await request_client.aclose()

    data = as_record(payload.get("data"))
    raw_users = data.get("list") if data is not None else None
    total = (
        read_optional_non_negative_int(data.get("total"))
        if data is not None
        else None
    )

    if not isinstance(raw_users, list) or total is None:
        raise FollowingUsersError("关注列表接口成功响应格式不正确")

    users = [
        user
        for value in raw_users
        if (user := _parse_following_user(value)) is not None
    ]
    return FollowingUsersResponse(
        users=users,
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total,
    )
