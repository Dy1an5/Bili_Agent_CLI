from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import qrcode

from bili_agent_cli.bilibili.auth import (
    BilibiliLoginError,
    create_qr_login,
    wait_for_qr_login,
)
from bili_agent_cli.bilibili.debug import BilibiliDebugError, request_bilibili_get
from bili_agent_cli.profile import (
    PROFILE_PATH,
    ProfileError,
    get_sessdata_cookie_header,
    load_profile,
    save_profile,
    validate_login_cookies,
)


def _positive_integer(value: str) -> int:
    number = int(value)

    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")

    return number


def _query_parameter(value: str) -> tuple[str, str]:
    name, separator, parameter_value = value.partition("=")

    if not separator or not name:
        raise argparse.ArgumentTypeError("参数格式必须是 KEY=VALUE")

    return name, parameter_value


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bili Agent CLI 调试工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="通过终端二维码登录 B站")
    login_parser.add_argument(
        "--timeout",
        type=_positive_integer,
        default=180,
        help="等待扫码的秒数，默认 180",
    )

    subparsers.add_parser(
        "profile",
        help="安全检查 privacy/profile.txt 登录资料",
    )

    get_parser = subparsers.add_parser("get", help="调试 B站 GET 接口")
    get_parser.add_argument(
        "path",
        help="api.bilibili.com 下的绝对路径",
    )
    get_parser.add_argument(
        "--param",
        action="append",
        default=[],
        type=_query_parameter,
        metavar="KEY=VALUE",
        help="查询参数，可重复使用",
    )

    return parser


def _print_qr_code(url: str) -> None:
    qr_code = qrcode.QRCode(border=1)
    qr_code.add_data(url)
    qr_code.make(fit=True)
    qr_code.print_ascii(invert=True)


def _print_login_status(status: int) -> None:
    if status == 86090:
        print("已扫码，请在手机上确认登录。", flush=True)
    else:
        print("等待扫码……", flush=True)


async def _run_login(timeout_seconds: int) -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        qr_login = await create_qr_login(client)
        print("请使用哔哩哔哩手机客户端扫描二维码并确认登录：", flush=True)
        _print_qr_code(qr_login.url)
        cookies = await wait_for_qr_login(
            client,
            qr_login,
            timeout_seconds=timeout_seconds,
            on_status_change=_print_login_status,
        )

    validate_login_cookies(cookies)
    profile_path = save_profile(cookies)
    print(f"登录成功，Cookie 已安全保存到 {profile_path}")


def _run_profile() -> None:
    cookies = load_profile()
    available_names = sorted(cookies)
    print(f"登录资料：{PROFILE_PATH}")
    print(f"当前用户 ID：{cookies['DedeUserID']}")
    print(f"Cookie 字段：{', '.join(available_names)}")
    print("必要 Cookie 字段完整，未输出会话或 CSRF Cookie 值。")


async def _run_get(
    path: str,
    parameters: list[tuple[str, str]],
) -> None:
    cookies = load_profile()
    sessdata_cookie = get_sessdata_cookie_header(cookies)
    payload = await request_bilibili_get(path, parameters, sessdata_cookie)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = _create_parser()
    arguments = parser.parse_args()

    try:
        if arguments.command == "login":
            asyncio.run(_run_login(arguments.timeout))
        elif arguments.command == "profile":
            _run_profile()
        elif arguments.command == "get":
            asyncio.run(_run_get(arguments.path, arguments.param))
    except (
        BilibiliDebugError,
        BilibiliLoginError,
        ProfileError,
        httpx.RequestError,
    ) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from None
