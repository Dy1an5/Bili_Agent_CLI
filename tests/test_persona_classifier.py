from __future__ import annotations

import pytest

from bili_agent_cli.agent.deepseek.errors import ProviderResponseError
from bili_agent_cli.persona.classifier import _parse_response


def payload(arguments: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_video_topic_classifications",
                                "arguments": arguments,
                            }
                        }
                    ]
                }
            }
        ]
    }


def test_classifier_accepts_known_leaf_and_filters_low_confidence() -> None:
    result = _parse_response(
        payload(
            """
            {"results":[{"source_id":"video:1","topics":[
              {"topic_key":"technology.ai","confidence":0.9,"evidence":"AI"},
              {"topic_key":"gaming.esports","confidence":0.2,"evidence":"弱"}
            ],"tags":[]}]}
            """
        ),
        {"video:1"},
    )

    assert [topic.topic_key for topic in result.results[0].topics] == [
        "technology.ai"
    ]


def test_classifier_rejects_unknown_or_missing_source_ids() -> None:
    with pytest.raises(ProviderResponseError):
        _parse_response(
            payload(
                """
                {"results":[{"source_id":"invented","topics":[
                  {"topic_key":"technology.ai","confidence":0.9,"evidence":"AI"}
                ],"tags":[]}]}
                """
            ),
            {"video:1"},
        )


def test_classifier_rejects_batch_without_valid_taxonomy_topic() -> None:
    with pytest.raises(ProviderResponseError):
        _parse_response(
            payload(
                """
                {"results":[{"source_id":"video:1","topics":[
                  {"topic_key":"invented.topic","confidence":0.9,"evidence":"X"}
                ],"tags":[]}]}
                """
            ),
            {"video:1"},
        )
