from __future__ import annotations

import unittest

from bili_agent_cli.agent.deepseek.errors import ProviderResponseError
from bili_agent_cli.agent.deepseek.model_client import parse_provider_usage
from bili_agent_cli.agent.deepseek.models import TokenUsage as LegacyTokenUsage
from bili_agent_cli.agent.models import AgentRunResponse, TokenUsage


class ProviderUsageParsingTest(unittest.TestCase):
    def test_maps_deepseek_usage_to_agent_usage(self) -> None:
        usage = parse_provider_usage(
            {
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": 45,
                }
            }
        )

        self.assertEqual(
            usage,
            TokenUsage(input_tokens=123, output_tokens=45),
        )
        self.assertIs(LegacyTokenUsage, TokenUsage)

    def test_rejects_missing_or_invalid_usage(self) -> None:
        invalid_payloads = [
            {},
            {"usage": None},
            {"usage": {}},
            {
                "usage": {
                    "prompt_tokens": "123",
                    "completion_tokens": 45,
                }
            },
            {
                "usage": {
                    "prompt_tokens": 123,
                    "completion_tokens": True,
                }
            },
            {
                "usage": {
                    "prompt_tokens": -1,
                    "completion_tokens": 45,
                }
            },
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ProviderResponseError):
                    parse_provider_usage(payload)


class AgentRunResponseUsageTest(unittest.TestCase):
    def test_usage_defaults_to_zero_and_is_not_serialized(self) -> None:
        response = AgentRunResponse(
            answer="回答",
            session_id="00000000-0000-0000-0000-000000000001",
        )

        self.assertEqual(
            response.usage,
            TokenUsage(input_tokens=0, output_tokens=0),
        )
        self.assertNotIn("usage", response.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
