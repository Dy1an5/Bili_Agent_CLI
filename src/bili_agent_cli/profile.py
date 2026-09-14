from __future__ import annotations

import os
import tempfile
from http.cookies import CookieError, SimpleCookie
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_DIR = PROJECT_ROOT / "secret"
PRIVACY_DIR = PROJECT_ROOT / "privacy"
PROFILE_PATH = SECRET_DIR / "profile.txt"
REQUIRED_LOGIN_COOKIE_NAMES = ("SESSDATA", "bili_jct", "DedeUserID")


class ProfileError(Exception):
    pass


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookie = SimpleCookie()

    try:
        cookie.load(cookie_header)
    except CookieError as error:
        raise ProfileError("profile.txt 中的 Cookie 格式不正确") from error

    values = {
        name: morsel.value
        for name, morsel in cookie.items()
        if name and morsel.value
    }

    if not values:
        raise ProfileError("profile.txt 中没有有效 Cookie")

    return values


def validate_login_cookies(cookies: dict[str, str]) -> None:
    missing_names = [
        name
        for name in REQUIRED_LOGIN_COOKIE_NAMES
        if not cookies.get(name)
    ]

    if missing_names:
        missing_text = ", ".join(missing_names)
        raise ProfileError(f"登录结果缺少必要 Cookie 字段: {missing_text}")


def serialize_cookies(cookies: dict[str, str]) -> str:
    validate_login_cookies(cookies)
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def save_profile(cookies: dict[str, str]) -> Path:
    content = serialize_cookies(cookies)
    PROFILE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=PROFILE_PATH.parent,
        prefix=".profile.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as profile_file:
            profile_file.write(content)
            profile_file.write("\n")
        temporary_path.chmod(0o600)
        temporary_path.replace(PROFILE_PATH)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return PROFILE_PATH


def load_profile() -> dict[str, str]:
    try:
        content = PROFILE_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise ProfileError(
            f"登录资料不存在，请先运行 login 命令: {PROFILE_PATH}"
        ) from error
    except OSError as error:
        raise ProfileError(f"无法读取登录资料: {error}") from error

    cookies = parse_cookie_header(content)
    validate_login_cookies(cookies)
    return cookies


def get_sessdata_cookie_header(cookies: dict[str, str]) -> str:
    sessdata = cookies.get("SESSDATA")

    if not sessdata:
        raise ProfileError("登录资料中没有有效的 SESSDATA")

    return f"SESSDATA={sessdata}"


def get_csrf_token(cookies: dict[str, str]) -> str:
    csrf_token = cookies.get("bili_jct", "").strip()

    if not csrf_token:
        raise ProfileError("登录资料中没有有效的 bili_jct")

    return csrf_token


def get_write_cookie_header(cookies: dict[str, str]) -> str:
    sessdata = cookies.get("SESSDATA", "").strip()
    csrf_token = get_csrf_token(cookies)

    if not sessdata:
        raise ProfileError("登录资料中没有有效的 SESSDATA")

    return f"SESSDATA={sessdata}; bili_jct={csrf_token}"


def get_user_id(cookies: dict[str, str]) -> str:
    user_id = cookies.get("DedeUserID", "").strip()

    if not user_id.isdigit() or int(user_id) <= 0:
        raise ProfileError("登录资料中没有有效的 DedeUserID")

    return user_id
