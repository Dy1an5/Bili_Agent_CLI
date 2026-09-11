from __future__ import annotations

import argparse

import httpx
import qrcode

from bili_agent_cli.bilibili.auth import create_qr_login, wait_for_qr_login
from bili_agent_cli.profile import (
    PROFILE_PATH,
    load_profile,
    save_profile,
    validate_login_cookies,
)


def _positive_integer(value: str) -> int:
    number = int(value)

    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")

    return number


def register_commands(subparsers: argparse._SubParsersAction) -> None:
    login_parser = subparsers.add_parser(
        "login",
        help="通过终端二维码登录 B站",
    )
    login_parser.add_argument(
        "--timeout",
        type=_positive_integer,
        default=180,
        help="等待扫码的秒数，默认 180",
    )
    login_parser.set_defaults(handler=run_login)

    profile_parser = subparsers.add_parser(
        "profile",
        help="安全检查 privacy/profile.txt 登录资料",
    )
    profile_parser.set_defaults(handler=run_profile)


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


async def run_login(arguments: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        qr_login = await create_qr_login(client)
        print("请使用哔哩哔哩手机客户端扫描二维码并确认登录：", flush=True)
        _print_qr_code(qr_login.url)
        cookies = await wait_for_qr_login(
            client,
            qr_login,
            timeout_seconds=arguments.timeout,
            on_status_change=_print_login_status,
        )

    validate_login_cookies(cookies)
    profile_path = save_profile(cookies)
    print(f"登录成功，Cookie 已安全保存到 {profile_path}")


async def run_profile(arguments: argparse.Namespace) -> None:
    del arguments

    cookies = load_profile()
    available_names = sorted(cookies)
    print(f"登录资料：{PROFILE_PATH}")
    print(f"当前用户 ID：{cookies['DedeUserID']}")
    print(f"Cookie 字段：{', '.join(available_names)}")
    print("必要 Cookie 字段完整，未输出会话或 CSRF Cookie 值。")
