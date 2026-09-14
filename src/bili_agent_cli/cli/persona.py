from __future__ import annotations

import argparse
import json

from bili_agent_cli.persona.models import GetUserProfileArgs
from bili_agent_cli.persona.service import PersonaService


persona_service = PersonaService()


def register_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("persona", help="查看或刷新用户画像")
    actions = parser.add_subparsers(dest="persona_action", required=True)

    show = actions.add_parser("show", help="读取最近一次画像快照")
    show.set_defaults(handler=run)

    refresh = actions.add_parser("refresh", help="从已入库数据增量刷新画像")
    refresh.add_argument(
        "--max-new-videos",
        type=int,
        default=60,
        choices=range(1, 201),
        metavar="1..200",
    )
    refresh.set_defaults(handler=run)


async def run(arguments: argparse.Namespace) -> None:
    refresh = arguments.persona_action == "refresh"
    result = await persona_service.get_profile(
        GetUserProfileArgs(
            refresh=refresh,
            max_new_videos=getattr(arguments, "max_new_videos", 60),
        )
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
