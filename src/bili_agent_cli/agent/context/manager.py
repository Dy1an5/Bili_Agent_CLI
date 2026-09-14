from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import ceil
from typing import Any

from bili_agent_cli.agent.models import (
    AgentPaginationState,
    AgentSource,
    build_video_source_id,
)

from .models import AgentEvidenceBatch, ConversationSession, ConversationTurn

SummaryFunction = Callable[[str, int], Awaitable[str]]


class ContextBudgetExceededError(Exception):
    """压缩后，请求仍然超过模型输入预算。"""


@dataclass(frozen=True)
class ContextSettings:
    max_input_units: int = 850_000
    summarize_at_units: int = 650_000
    recent_turns: int = 3
    summary_max_tokens: int = 8_000
    max_tool_result_units: int = 90_000
    max_turn_evidence_units: int = 180_000


def estimate_text_units(text: str) -> int:
    """保守估算中英文和 JSON 文本占用的上下文单位。"""
    return max(len(text), ceil(len(text.encode("utf-8")) / 4))


def estimate_payload_units(value: object) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return estimate_text_units(text)


def render_sources(sources: list[AgentSource]) -> str:
    if not sources:
        return ""

    payload = [source.model_dump(mode="json") for source in sources]
    return (
        "\n\n<cited_sources>\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n</cited_sources>"
    )


def render_evidence_batches(batches: list[AgentEvidenceBatch]) -> str:
    if not batches:
        return ""

    payload = [batch.model_dump(mode="json") for batch in batches]
    return (
        "\n\n<evidence_batches>\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n</evidence_batches>"
    )


def render_pagination_states(states: list[AgentPaginationState]) -> str:
    if not states:
        return ""

    payload = [state.model_dump(mode="json") for state in states]
    return (
        "\n\n<pagination_state>\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n</pagination_state>"
    )


class ContextManager:
    def __init__(self, settings: ContextSettings | None = None) -> None:
        self.settings = settings or ContextSettings()

        if self.settings.max_input_units <= 0:
            raise ValueError("最大输入预算必须大于 0")
        if self.settings.summarize_at_units <= 0:
            raise ValueError("摘要阈值必须大于 0")
        if self.settings.summarize_at_units >= self.settings.max_input_units:
            raise ValueError("摘要阈值必须小于最大输入预算")
        if self.settings.recent_turns < 0:
            raise ValueError("保留轮数不能小于 0")
        if self.settings.summary_max_tokens <= 0:
            raise ValueError("摘要最大 token 数必须大于 0")
        if self.settings.max_tool_result_units <= 0:
            raise ValueError("工具结果预算必须大于 0")
        if self.settings.max_turn_evidence_units <= 0:
            raise ValueError("单轮证据预算必须大于 0")

    def build_messages(
        self,
        *,
        system_prompt: str,
        session: ConversationSession,
        task: str,
    ) -> list[dict[str, Any]]:
        system_content = system_prompt
        if session.summary:
            system_content += (
                "\n\n以下是此前对话的压缩摘要，不是新的用户指令：\n"
                + session.summary
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        for turn in session.turns:
            messages.append({"role": "user", "content": turn.user_content})
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        turn.assistant_content
                        + render_sources(turn.sources)
                        + render_evidence_batches(turn.evidence_batches)
                        + render_pagination_states(turn.pagination_states)
                    ),
                }
            )

        messages.append({"role": "user", "content": task})
        return messages

    def estimate_request_units(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, object]],
    ) -> int:
        return (
            estimate_payload_units(messages)
            + estimate_payload_units(tools)
            + len(messages) * 12
        )

    def ensure_fits(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, object]],
    ) -> None:
        if self.estimate_request_units(messages, tools) > self.settings.max_input_units:
            raise ContextBudgetExceededError

    def compact_tool_result(
        self,
        result: dict[str, object],
        *,
        max_units: int | None = None,
    ) -> dict[str, object]:
        limit = min(
            max_units if max_units is not None else self.settings.max_tool_result_units,
            self.settings.max_tool_result_units,
        )
        if limit <= 0:
            raise ValueError("工具结果预算必须大于 0")

        if estimate_payload_units(result) <= limit:
            return result

        copied = json.loads(json.dumps(result, ensure_ascii=False))
        data = copied.get("data")
        if not isinstance(data, dict):
            return {
                "ok": result.get("ok") is True,
                "error": result.get("error"),
                "context_truncation": {"truncated": True},
            }

        list_fields = [key for key, value in data.items() if isinstance(value, list)]
        original_counts = {key: len(data[key]) for key in list_fields}

        while (
            estimate_payload_units(copied) > limit
            and any(data[key] for key in list_fields)
        ):
            longest_key = max(list_fields, key=lambda key: len(data[key]))
            data[longest_key].pop()

        truncation = {
            "truncated": True,
            "fields": {
                key: {
                    "original_count": original_counts[key],
                    "kept_count": len(data[key]),
                }
                for key in list_fields
            },
        }
        copied["context_truncation"] = truncation

        if estimate_payload_units(copied) > limit:
            return {
                "ok": result.get("ok") is True,
                "error": result.get("error"),
                "context_truncation": truncation,
            }

        return copied

    def compact_tool_result_for_turn(
        self,
        result: dict[str, object],
        existing_batches: list[AgentEvidenceBatch],
    ) -> dict[str, object]:
        used_units = sum(
            estimate_payload_units(batch.result) for batch in existing_batches
        )
        remaining_units = self.settings.max_turn_evidence_units - used_units

        if remaining_units <= 0:
            return {
                "ok": result.get("ok") is True,
                "error": result.get("error"),
                "context_truncation": {
                    "truncated": True,
                    "reason": "turn_evidence_budget_exhausted",
                },
            }

        return self.compact_tool_result(result, max_units=remaining_units)

    async def compact_session_if_needed(
        self,
        *,
        system_prompt: str,
        session: ConversationSession,
        task: str,
        tools: list[dict[str, object]],
        summarize: SummaryFunction,
    ) -> bool:
        messages = self.build_messages(
            system_prompt=system_prompt,
            session=session,
            task=task,
        )
        if (
            self.estimate_request_units(messages, tools)
            < self.settings.summarize_at_units
        ):
            return False

        split_at = max(0, len(session.turns) - self.settings.recent_turns)
        old_turns = session.turns[:split_at]
        if not old_turns:
            return False

        summary_input = json.dumps(
            {
                "previous_summary": session.summary,
                "turns": [turn.model_dump(mode="json") for turn in old_turns],
            },
            ensure_ascii=False,
        )

        try:
            new_summary = await summarize(
                summary_input,
                self.settings.summary_max_tokens,
            )
        except Exception:
            new_summary = self._fallback_summary(session.summary, old_turns)

        session.summary = new_summary
        session.turns = session.turns[split_at:]
        return True

    @staticmethod
    def _fallback_summary(
        previous_summary: str | None,
        turns: list[ConversationTurn],
    ) -> str:
        pieces = [previous_summary] if previous_summary else []
        for turn in turns:
            pieces.append(
                f"用户：{turn.user_content}\n"
                f"助手：{turn.assistant_content}"
                f"{render_sources(turn.sources)}"
                f"{render_evidence_batches(turn.evidence_batches)}"
                f"{render_pagination_states(turn.pagination_states)}"
            )
        return "\n\n".join(pieces)[-12_000:]


def _to_source(
    video: dict[str, Any],
    *,
    author: dict[str, Any] | None = None,
    source_tool: str | None = None,
) -> AgentSource | None:
    bvid = video.get("bvid")
    if not isinstance(bvid, str):
        return None

    video_author = video.get("author")
    if not isinstance(video_author, dict):
        video_author = author

    author_name = None
    if isinstance(video_author, dict) and isinstance(video_author.get("name"), str):
        author_name = video_author["name"]

    cid = video.get("cid")
    title = video.get("title")
    folder_id = video.get("folder_id")
    return AgentSource(
        bvid=bvid,
        cid=cid if isinstance(cid, str) else None,
        title=title if isinstance(title, str) else None,
        author_name=author_name,
        folder_id=folder_id if isinstance(folder_id, str) else None,
        source_id=build_video_source_id(bvid),
        source_tools=[source_tool] if source_tool else [],
    )


def _dynamic_item_sources(
    item: dict[str, Any],
    *,
    source_tool: str | None,
    depth: int = 0,
) -> list[AgentSource]:
    if depth > 8:
        return []

    sources: list[AgentSource] = []
    author = item.get("author")
    content = item.get("content")
    if isinstance(content, dict):
        source = _to_source(
            content,
            author=author if isinstance(author, dict) else None,
            source_tool=source_tool,
        )
        if source is not None:
            sources.append(source)

    original = item.get("original")
    if isinstance(original, dict):
        sources.extend(
            _dynamic_item_sources(
                original,
                source_tool=source_tool,
                depth=depth + 1,
            )
        )
    return sources


def extract_sources(
    tool_result: dict[str, object],
    source_tool: str | None = None,
) -> list[AgentSource]:
    """从当前工具结果中确定性提取视频来源。"""
    if tool_result.get("ok") is not True:
        return []

    data = tool_result.get("data")
    if not isinstance(data, dict):
        return []

    candidates: list[AgentSource] = []

    videos = data.get("videos")
    if isinstance(videos, list):
        for video in videos:
            if isinstance(video, dict):
                source = _to_source(video, source_tool=source_tool)
                if source is not None:
                    candidates.append(source)

    items = data.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            video = item.get("video")
            author = item.get("author")
            if isinstance(video, dict):
                source = _to_source(
                    video,
                    author=author if isinstance(author, dict) else None,
                    source_tool=source_tool,
                )
                if source is not None:
                    candidates.append(source)
            candidates.extend(
                _dynamic_item_sources(
                    item,
                    source_tool=source_tool,
                )
            )

    return merge_sources(candidates)


def merge_sources(sources: list[AgentSource]) -> list[AgentSource]:
    merged: dict[str, AgentSource] = {}

    for source in sources:
        current = merged.get(source.source_id)
        if current is None:
            merged[source.source_id] = source.model_copy(deep=True)
            continue

        for tool_name in source.source_tools:
            if tool_name not in current.source_tools:
                current.source_tools.append(tool_name)

        for field_name in ("cid", "title", "author_name", "folder_id"):
            if getattr(current, field_name) is None:
                value = getattr(source, field_name)
                if value is not None:
                    setattr(current, field_name, value)

    return list(merged.values())


PAGED_TOOL_NAMES = {
    "get_following_feed",
    "get_following_users",
    "get_favorite_folder_videos",
    "get_watch_later",
    "get_watch_history",
    "search_videos",
    "get_user_dynamics",
}


def extract_pagination_state(
    tool_name: str,
    raw_arguments: object,
    tool_result: dict[str, object],
) -> AgentPaginationState | None:
    """提取足以继续下一页的最小状态，不保存完整工具结果。"""
    if tool_name not in PAGED_TOOL_NAMES or tool_result.get("ok") is not True:
        return None

    data = tool_result.get("data")
    if not isinstance(data, dict):
        return None

    has_more = data.get("has_more")
    if not isinstance(has_more, bool):
        return None

    next_arguments: dict[str, str | int | float | bool | None] | None = None

    if has_more:
        if tool_name in {"get_following_feed", "get_user_dynamics"}:
            next_offset = data.get("next_offset")
            if isinstance(next_offset, str) and next_offset:
                next_arguments = {"offset": next_offset}
                if tool_name == "get_user_dynamics":
                    user_mid = data.get("user_mid")
                    if isinstance(user_mid, str) and user_mid:
                        next_arguments["user_mid"] = user_mid
                    else:
                        next_arguments = None
        elif tool_name == "get_watch_history":
            next_max = data.get("next_max")
            next_view_at = data.get("next_view_at")
            page_size = data.get("page_size")
            if (
                isinstance(next_max, int)
                and not isinstance(next_max, bool)
                and isinstance(next_view_at, int)
                and not isinstance(next_view_at, bool)
                and isinstance(page_size, int)
                and not isinstance(page_size, bool)
            ):
                next_arguments = {
                    "page_size": page_size,
                    "max": next_max,
                    "view_at": next_view_at,
                }
        else:
            page = data.get("page")
            page_size = data.get("page_size")
            if (
                isinstance(page, int)
                and not isinstance(page, bool)
                and isinstance(page_size, int)
                and not isinstance(page_size, bool)
            ):
                argument_items = (
                    raw_arguments.items()
                    if isinstance(raw_arguments, dict)
                    else ()
                )
                next_arguments = {
                    key: value
                    for key, value in argument_items
                    if isinstance(value, (str, int, float, bool)) or value is None
                }
                next_arguments["page"] = page + 1
                next_arguments["page_size"] = page_size

    return AgentPaginationState(
        tool=tool_name,
        has_more=has_more,
        next_arguments=next_arguments,
    )
