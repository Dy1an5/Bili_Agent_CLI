from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bili_agent_cli.agent.deepseek.errors import ProviderResponseError
from bili_agent_cli.agent.deepseek.model_client import (
    create_memory_extraction,
    parse_memory_extraction_message,
)
from bili_agent_cli.agent.memory.models import MemoryCandidate
from bili_agent_cli.agent.memory.prompts import (
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    MEMORY_EXTRACTION_TOOL_NAME,
)
from bili_agent_cli.agent.memory.service import filter_memory_candidates


def extraction_message(arguments: object) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "memory-call-1",
                "type": "function",
                "function": {
                    "name": MEMORY_EXTRACTION_TOOL_NAME,
                    "arguments": (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, ensure_ascii=False)
                    ),
                },
            }
        ],
    }


def candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "key": "response.length",
        "kind": "constraint",
        "content": "以后回答保持简短",
        "topics": [],
        "confidence": 1.0,
        "evidence_quote": "以后回答保持简短",
        "durability": "explicit",
        "scope": "global",
        "scope_value": None,
    }
    payload.update(overrides)
    return payload


class MemoryExtractionParsingTest(unittest.TestCase):
    def test_parses_valid_candidates(self) -> None:
        extraction = parse_memory_extraction_message(
            extraction_message(
                {
                    "candidates": [
                        candidate_payload(topics=["回答风格"])
                    ]
                }
            )
        )

        self.assertEqual(len(extraction.candidates), 1)
        self.assertEqual(extraction.candidates[0].key, "response.length")
        self.assertEqual(extraction.candidates[0].kind.value, "constraint")

    def test_parses_empty_candidate_list(self) -> None:
        extraction = parse_memory_extraction_message(
            extraction_message({"candidates": []})
        )

        self.assertEqual(extraction.candidates, [])

    def test_rejects_invalid_provider_messages(self) -> None:
        invalid_messages = (
            None,
            {},
            {"tool_calls": []},
            {"tool_calls": [{"function": None}]},
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "wrong_tool",
                            "arguments": '{"candidates":[]}',
                        }
                    }
                ]
            },
            extraction_message("not-json"),
            extraction_message(
                {
                    "candidates": [
                        candidate_payload(key="INVALID KEY")
                    ]
                }
            ),
        )

        for message in invalid_messages:
            with self.subTest(message=message):
                with self.assertRaises(ProviderResponseError):
                    parse_memory_extraction_message(message)

    def test_filters_sensitive_candidates_without_logging_values(self) -> None:
        candidates = [
            MemoryCandidate.model_validate(candidate_payload()),
            MemoryCandidate.model_validate(
                candidate_payload(
                    key="account.cookie",
                    kind="fact",
                    content="保存登录信息",
                    evidence_quote="保存登录信息",
                    scope="topic",
                    scope_value="account",
                )
            ),
            MemoryCandidate.model_validate(
                candidate_payload(
                    key="account.note",
                    kind="fact",
                    content="SESSDATA 是敏感凭据",
                    evidence_quote="SESSDATA 是敏感凭据",
                    scope="topic",
                    scope_value="account",
                )
            ),
            MemoryCandidate.model_validate(
                candidate_payload(
                    key="developer.preference",
                    kind="preference",
                    content="用户喜欢隐藏 API Key",
                    evidence_quote="用户喜欢隐藏 API Key",
                    confidence=0.8,
                )
            ),
            MemoryCandidate.model_validate(
                candidate_payload(
                    key="account.preference",
                    kind="preference",
                    content="用户希望保持登录",
                    evidence_quote="用户希望保持登录",
                    topics=["access_token=private-value"],
                    confidence=0.8,
                )
            ),
        ]

        filtered = filter_memory_candidates(candidates)

        self.assertEqual([item.key for item in filtered], ["response.length"])


class MemoryExtractionRequestTest(unittest.TestCase):
    def test_sends_forced_tool_schema_and_parses_response(self) -> None:
        provider_message = extraction_message({"candidates": []})
        response = httpx.Response(
            200,
            json={"choices": [{"message": provider_message}]},
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response

        with (
            patch(
                "bili_agent_cli.agent.deepseek.model_client.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "bili_agent_cli.agent.deepseek.model_client.load_api_key",
                return_value="test-key",
            ),
        ):
            result = asyncio.run(create_memory_extraction("提取输入"))

        self.assertEqual(result.candidates, [])
        request = client.post.await_args
        self.assertEqual(request.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(
            request.kwargs["headers"]["Authorization"],
            "Bearer test-key",
        )
        body = request.kwargs["json"]
        self.assertEqual(
            body["messages"][0]["content"],
            MEMORY_EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertEqual(body["messages"][1]["content"], "提取输入")
        self.assertEqual(
            body["tools"][0]["function"]["name"],
            MEMORY_EXTRACTION_TOOL_NAME,
        )
        self.assertEqual(
            body["tool_choice"]["function"]["name"],
            MEMORY_EXTRACTION_TOOL_NAME,
        )
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertIn(
            "candidates",
            body["tools"][0]["function"]["parameters"]["properties"],
        )
        candidate_schema = body["tools"][0]["function"]["parameters"]["$defs"][
            "MemoryCandidate"
        ]
        self.assertIn("evidence_quote", candidate_schema["required"])
        self.assertIn("durability", candidate_schema["required"])
        self.assertIn("scope", candidate_schema["required"])
        self.assertIn("scope_value", candidate_schema["required"])


if __name__ == "__main__":
    unittest.main()
