from __future__ import annotations

import argparse
import json
import shlex
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from bili_agent_cli.agent.agent_loop import memory_store, run_agent, session_store
from bili_agent_cli.agent.context import (
    ContextBudgetExceededError,
    SessionNotFoundError,
    SessionStorageError,
)
from bili_agent_cli.agent.deepseek.errors import ModelCallError
from bili_agent_cli.agent.memory import (
    MemoryNotFoundError,
    MemoryState,
    MemoryStorageError,
)
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
    print(
        "输入 /new 开始新会话，/context 查看上下文，"
        "/memory 管理记忆，/exit 退出。"
    )

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

        if task == "/memory" or task.startswith("/memory "):
            _handle_memory_command(task)
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
        parts: list[str] = []
        if result.active_saved_count:
            parts.append(f"新增 {result.active_saved_count} 条")
        if result.promoted_count:
            parts.append(f"自动确认 {result.promoted_count} 条")
        if result.updated_count:
            parts.append(f"更新 {result.updated_count} 条")
        detail = "，".join(parts) or f"处理 {result.saved_count} 条"
        print(f"记忆> 已更新长期记忆：{detail}。")
        return

    if result.status == AgentMemoryStatus.PENDING:
        print(
            f"记忆> 新增/更新 {result.pending_saved_count} 条待确认候选；"
            "使用 /memory pending 查看。"
        )
        return

    if result.status == AgentMemoryStatus.MIXED:
        print(
            f"记忆> 已激活 {result.saved_count} 条，"
            f"另有 {result.pending_saved_count} 条待确认候选。"
        )
        return

    if result.status == AgentMemoryStatus.NO_CANDIDATES:
        return

    if result.status == AgentMemoryStatus.FILTERED:
        print("记忆> 候选内容被安全规则过滤，未保存。", file=sys.stderr)
        return

    detail = result.error_code or result.status.value
    print(f"记忆> 保存失败（{detail}）。", file=sys.stderr)


def _handle_memory_command(command: str) -> None:
    try:
        parts = shlex.split(command)
    except ValueError as error:
        print(f"记忆> 命令格式错误：{error}", file=sys.stderr)
        return

    arguments = parts[1:]
    if not arguments:
        _print_memory_help()
        return

    action = arguments[0].lower()
    try:
        if action == "pending":
            _print_memory_list({MemoryState.PENDING})
        elif action == "list":
            selection = arguments[1].lower() if len(arguments) > 1 else "active"
            states = {
                "active": {MemoryState.ACTIVE},
                "pending": {MemoryState.PENDING},
                "all": set(MemoryState),
            }.get(selection)
            if states is None or len(arguments) > 2:
                raise ValueError("用法：/memory list [active|pending|all]")
            _print_memory_list(states)
        elif action == "show" and len(arguments) == 2:
            _print_memory_detail(UUID(arguments[1]))
        elif action == "confirm" and len(arguments) == 2:
            item = memory_store.confirm(UUID(arguments[1]))
            print(f"记忆> 已确认：{item.content}")
        elif action == "reject" and len(arguments) == 2:
            item = memory_store.soft_delete(UUID(arguments[1]))
            print(f"记忆> 已拒绝候选：{item.content}")
        elif action == "edit" and len(arguments) >= 3:
            item = memory_store.update_memory(
                UUID(arguments[1]), " ".join(arguments[2:])
            )
            print(f"记忆> 已修改并激活：{item.content}")
        elif action == "delete" and len(arguments) == 2:
            item = memory_store.soft_delete(UUID(arguments[1]))
            print(f"记忆> 已软删除：{item.content}")
        elif action == "restore" and len(arguments) == 2:
            item = memory_store.restore(UUID(arguments[1]))
            print(f"记忆> 已恢复并激活：{item.content}")
        elif action == "clear" and arguments[1:] == ["--yes"]:
            count = memory_store.clear_live()
            print(f"记忆> 已软删除 {count} 条 active/pending 记忆。")
        else:
            raise ValueError("未知命令或参数数量不正确；输入 /memory 查看帮助")
    except (MemoryNotFoundError, MemoryStorageError, ValueError) as error:
        print(f"记忆> 操作失败：{error}", file=sys.stderr)


def _print_memory_help() -> None:
    print(
        "记忆命令：\n"
        "  /memory list [active|pending|all]\n"
        "  /memory pending\n"
        "  /memory show <id>\n"
        "  /memory confirm <id>\n"
        "  /memory reject <id>\n"
        "  /memory edit <id> <新内容>\n"
        "  /memory delete <id>\n"
        "  /memory restore <id>\n"
        "  /memory clear --yes"
    )


def _print_memory_list(states: set[MemoryState]) -> None:
    items = memory_store.list_memories(states=states)
    if not items:
        print("记忆> 没有匹配的记录。")
        return
    for item in items:
        scope = item.scope.value
        if item.scope_value:
            scope += f":{item.scope_value}"
        print(
            f"{item.id}  {item.state.value:<10} {item.kind.value:<10} "
            f"{scope:<28} evidence={item.evidence_count}  {item.content}"
        )


def _print_memory_detail(memory_id: UUID) -> None:
    item = memory_store.get_memory(memory_id)
    print(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2))
    evidence = memory_store.list_evidence(memory_id)
    if evidence:
        print("证据：")
        for record in evidence:
            print(f"- {record.source_ref}: {record.user_excerpt}")


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
