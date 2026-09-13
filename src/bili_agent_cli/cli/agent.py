from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from bili_agent_cli.agent.agent_loop import run_agent, session_store
from bili_agent_cli.agent.context import (
    ContextBudgetExceededError,
    SessionNotFoundError,
    SessionStorageError,
)
from bili_agent_cli.agent.deepseek.errors import ModelCallError
from bili_agent_cli.agent.models import AgentMemoryResult, AgentMemoryStatus


if TYPE_CHECKING:
    from prompt_toolkit import PromptSession


RECOVERABLE_CHAT_ERRORS = (
    ContextBudgetExceededError,
    ModelCallError,
    SessionNotFoundError,
    SessionStorageError,
)

_prompt_session: PromptSession[str] | None = None
_enhanced_input_disabled = False


def _get_prompt_session() -> PromptSession[str]:
    """懒加载 prompt_toolkit 会话，避免非交互命令也去碰终端。"""

    global _prompt_session

    if _prompt_session is None:
        from prompt_toolkit import PromptSession

        _prompt_session = PromptSession()

    return _prompt_session


async def _read_task(prompt: str) -> str:
    """读取一条用户输入。

    真终端交给 prompt_toolkit：它按显示宽度计算重绘位置，汉字退格不会残留。
    标准输入或输出不是终端时（管道、重定向、测试）退回内置 input。
    """

    global _enhanced_input_disabled

    if _enhanced_input_disabled or not (
        sys.stdin.isatty() and sys.stdout.isatty()
    ):
        return input(prompt)

    try:
        return await _get_prompt_session().prompt_async(prompt)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        _enhanced_input_disabled = True
        print(
            "提示：当前终端不支持增强输入，已退回基础输入模式。",
            file=sys.stderr,
        )

        return input(prompt)


def register_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "agent",
        help="启动 Agent 多轮对话",
    )
    parser.set_defaults(handler=run)


async def run(arguments: argparse.Namespace) -> None:
    del arguments

    session_id = None
    print("输入 /new 开始新会话，/context 查看上下文，/exit 退出。")

    while True:
        try:
            task = (await _read_task("你> ")).strip()
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

        if task == "/context":
            try:
                await _print_context(session_id)
            except SessionNotFoundError:
                session_id = None
                print("错误：当前会话已过期，请重新开始。", file=sys.stderr)
            except SessionStorageError as error:
                print(f"错误：{_format_chat_error(error)}", file=sys.stderr)
            continue

        try:
            response = await run_agent(task, session_id)
        except RECOVERABLE_CHAT_ERRORS as error:
            if isinstance(error, SessionNotFoundError):
                session_id = None
            print(f"错误：{_format_chat_error(error)}", file=sys.stderr)
            continue

        session_id = response.session_id
        print(f"助手> {response.answer}")
        _print_memory_result(response.memory)


def _print_memory_result(result: AgentMemoryResult) -> None:
    if result.status == AgentMemoryStatus.NOT_ATTEMPTED:
        return

    if result.status == AgentMemoryStatus.SAVED:
        print(f"记忆> 已保存 {result.saved_count} 条长期记忆。")
        return

    if result.status == AgentMemoryStatus.NO_CANDIDATES:
        print("记忆> 本轮未提取到需要长期保存的信息。")
        return

    if result.status == AgentMemoryStatus.FILTERED:
        print("记忆> 候选内容被安全规则过滤，未保存。", file=sys.stderr)
        return

    detail = result.error_code or result.status.value
    print(f"记忆> 保存失败（{detail}）。", file=sys.stderr)


def _format_chat_error(error: Exception) -> str:
    if isinstance(error, ContextBudgetExceededError):
        return "当前上下文超过限制，请输入 /new 开始新会话。"

    if isinstance(error, SessionNotFoundError):
        return "当前会话已过期，下一条消息将开始新会话。"

    if isinstance(error, SessionStorageError):
        return "会话文件无法读取或写入，请检查 privacy/conversations。"

    return str(error).strip() or error.__class__.__name__


async def _print_context(session_id: UUID | None) -> None:
    if session_id is None:
        print("当前还没有已完成的对话。")
        return

    session = await session_store.get(session_id)
    snapshot = {
        "session_id": str(session.id),
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "summary": session.summary,
        "turns": [turn.model_dump(mode="json") for turn in session.turns],
        "pending_turn": (
            session.pending_turn.model_dump(mode="json")
            if session.pending_turn is not None
            else None
        ),
    }
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
