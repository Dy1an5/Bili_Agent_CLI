from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx

from bili_agent_cli.agent.context import (
    ContextBudgetExceededError,
    SessionNotFoundError,
)
from bili_agent_cli.agent.deepseek.errors import ModelCallError
from bili_agent_cli.bilibili.auth import BilibiliLoginError
from bili_agent_cli.bilibili.debug import BilibiliDebugError
from bili_agent_cli.profile import ProfileError
from bili_agent_cli.persona.service import PersonaError
from bili_agent_cli.content.store import ContentStorageError
from bili_agent_cli.agent.memory.store import MemoryStorageError

from . import agent, auth, debug, persona


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bili Agent CLI 调试工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth.register_commands(subparsers)
    debug.register_command(subparsers)
    agent.register_command(subparsers)
    persona.register_command(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _create_parser().parse_args(argv)

    try:
        asyncio.run(arguments.handler(arguments))
    except (
        BilibiliDebugError,
        BilibiliLoginError,
        ContextBudgetExceededError,
        ModelCallError,
        ProfileError,
        PersonaError,
        ContentStorageError,
        MemoryStorageError,
        SessionNotFoundError,
        httpx.RequestError,
    ) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
