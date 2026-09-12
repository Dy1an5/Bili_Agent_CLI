from __future__ import annotations

import json

from .models import ConversationSession, ConversationTurn


def _indented_json(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return "\n".join(f"    {line}" for line in rendered.splitlines())


def _render_turn(turn: ConversationTurn, index: int | None = None) -> str:
    title = "Pending turn" if index is None else f"Turn {index}"
    pieces = [
        f"## {title}",
        "",
        f"- ID: `{turn.id}`",
        f"- Status: `{turn.status}`",
        f"- Created: `{turn.created_at.isoformat()}`",
        f"- Updated: `{turn.updated_at.isoformat()}`",
    ]
    if turn.error_code:
        pieces.append(f"- Error: `{turn.error_code}`")

    pieces.extend(["", "### User", "", turn.user_content])
    if turn.assistant_content:
        pieces.extend(["", "### Assistant", "", turn.assistant_content])
    if turn.sources:
        pieces.extend(
            [
                "",
                "### Cited sources",
                "",
                _indented_json(
                    [source.model_dump(mode="json") for source in turn.sources]
                ),
            ]
        )
    if turn.evidence_batches:
        pieces.extend(
            [
                "",
                "### Evidence batches",
                "",
                _indented_json(
                    [
                        batch.model_dump(mode="json")
                        for batch in turn.evidence_batches
                    ]
                ),
            ]
        )
    if turn.pagination_states:
        pieces.extend(
            [
                "",
                "### Pagination states",
                "",
                _indented_json(
                    [
                        state.model_dump(mode="json")
                        for state in turn.pagination_states
                    ]
                ),
            ]
        )
    return "\n".join(pieces)


def render_session_markdown(session: ConversationSession) -> str:
    pieces = [
        "---",
        "schema_version: 1",
        f'session_id: "{session.id}"',
        f'created_at: "{session.created_at.isoformat()}"',
        f'updated_at: "{session.updated_at.isoformat()}"',
        "---",
        "",
        f"# Conversation {session.id}",
    ]

    if session.summary:
        pieces.extend(["", "## Summary", "", session.summary])

    for index, turn in enumerate(session.turns, start=1):
        pieces.extend(["", _render_turn(turn, index)])

    if session.pending_turn is not None:
        pieces.extend(["", _render_turn(session.pending_turn)])

    return "\n".join(pieces).rstrip() + "\n"
