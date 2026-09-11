from __future__ import annotations

import argparse
import json

from bili_agent_cli.bilibili.debug import request_bilibili_get
from bili_agent_cli.profile import get_sessdata_cookie_header, load_profile


def _query_parameter(value: str) -> tuple[str, str]:
    name, separator, parameter_value = value.partition("=")

    if not separator or not name:
        raise argparse.ArgumentTypeError("参数格式必须是 KEY=VALUE")

    return name, parameter_value


def register_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("get", help="调试 B站 GET 接口")
    parser.add_argument(
        "path",
        help="api.bilibili.com 下的绝对路径",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        type=_query_parameter,
        metavar="KEY=VALUE",
        help="查询参数，可重复使用",
    )
    parser.set_defaults(handler=run)


async def run(arguments: argparse.Namespace) -> None:
    cookies = load_profile()
    sessdata_cookie = get_sessdata_cookie_header(cookies)
    payload = await request_bilibili_get(
        arguments.path,
        arguments.param,
        sessdata_cookie,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
