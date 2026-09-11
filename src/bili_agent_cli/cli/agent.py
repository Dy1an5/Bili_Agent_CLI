from __future__ import annotations

import argparse

from bili_agent_cli.agent.agent_loop import run_agent


def register_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "agent",
        help="启动 Agent 多轮对话",
    )
    parser.set_defaults(handler=run)


async def run(arguments: argparse.Namespace) -> None:
    del arguments

    session_id = None
    print("输入 /new 开始新会话，输入 /exit 退出。")

    while True:
        try:
            task = input("你> ").strip()
        except EOFError:
            print()
            break

        if not task:
            continue

        if task == "/exit":
            break

        if task == "/new":
            session_id = None
            print("已切换到新会话")
            continue

        response = await run_agent(task, session_id)
        session_id = response.session_id
        print(f"助手> {response.answer}")
