from __future__ import annotations

import json
from typing import Any

from bili_agent_cli.agent.models import AgentSource

from .models import ConversationSession, ConversationTurn

def render_sources(sources: list[AgentSource]) -> str:
    if not sources:
        return ""

    payload = []
    for source in sources:
        payload.append(source.model_dump(mode="json"))

    return (
        "\n\n<evidence>\n"
        + json.dumps(
            payload, 
            ensure_ascii=False, 
            separators=(",", ":"),
        )
        + "\n</evidence>"
    )

class ContextManager:
    def build_messages(
        self,
        *,
        system_prompt: str,
        session: ConversationSession,
        task: str
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for turn in session.turns:
            messages.append(
                {
                    "role": "user",
                    "content": turn.user_content,
                }
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": turn.assistant_content + render_sources(turn.sources),
                }
            )

        messages.append(
            {
                "role": "user",
                "content": task,
            }
        )

        return messages

def _to_source(
    video: dict[str, Any],
    *,
    author: dict[str, Any] | None = None,
) -> AgentSource | None:
    bvid = video.get("bvid")

    if not isinstance(bvid, str):
        return None

    video_author = video.get("author")

    if not isinstance(video_author, dict):
        video_author = author

    author_name = None

    if (
        isinstance(video_author, dict)
        and isinstance(video_author.get("name"), str)
    ):
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
    )

def extract_sources(
    tool_result: dict[str, object],
) -> list[AgentSource]:
    """从当前工具结果中确定性提取视频来源。"""

    if tool_result.get("ok") is not True:
        return []

    data = tool_result.get("data")

    if not isinstance(data, dict):
        return []

    candidates: list[AgentSource] = []

    # search / favorites / watch_later
    # 都使用 data.videos。
    videos = data.get("videos")

    if isinstance(videos, list):
        for video in videos:
            if not isinstance(video, dict):
                continue

            source = _to_source(video)

            if source is not None:
                candidates.append(source)

    # following 使用：
    #
    # data.items[].video
    # data.items[].author
    items = data.get("items")

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue

            video = item.get("video")
            author = item.get("author")

            if not isinstance(video, dict):
                continue

            source = _to_source(
                video,
                author=(
                    author
                    if isinstance(author, dict)
                    else None
                ),
            )

            if source is not None:
                candidates.append(source)

    # 去重。
    #
    # 同一个 bvid + cid 只保留一次。
    result: list[AgentSource] = []

    seen: set[
        tuple[str, str | None]
    ] = set()

    for source in candidates:
        key = (
            source.bvid,
            source.cid,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(source)

    return result
    