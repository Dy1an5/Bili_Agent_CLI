import os

from .errors import ConfigurationError

API_KEY_ENV_NAME = "DEEPSEEK_API_KEY"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


def _load_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc

    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value


AGENT_MAX_OUTPUT_TOKENS = _load_positive_int(
    "AGENT_MAX_OUTPUT_TOKENS",
    32_000,
)
AGENT_CONTEXT_MAX_UNITS = _load_positive_int(
    "AGENT_CONTEXT_MAX_UNITS",
    850_000,
)
AGENT_CONTEXT_SUMMARIZE_AT_UNITS = _load_positive_int(
    "AGENT_CONTEXT_SUMMARIZE_AT_UNITS",
    650_000,
)
AGENT_CONTEXT_RECENT_TURNS = _load_positive_int(
    "AGENT_CONTEXT_RECENT_TURNS",
    3,
)
AGENT_CONTEXT_SUMMARY_MAX_TOKENS = _load_positive_int(
    "AGENT_CONTEXT_SUMMARY_MAX_TOKENS",
    8_000,
)
AGENT_CONTEXT_TOOL_RESULT_UNITS = _load_positive_int(
    "AGENT_CONTEXT_TOOL_RESULT_UNITS",
    90_000,
)
AGENT_CONTEXT_TURN_EVIDENCE_UNITS = _load_positive_int(
    "AGENT_CONTEXT_TURN_EVIDENCE_UNITS",
    180_000,
)
AGENT_SESSION_TTL_SECONDS = _load_positive_int(
    "AGENT_SESSION_TTL_SECONDS",
    3_600,
)
AGENT_SESSION_CAPACITY = _load_positive_int(
    "AGENT_SESSION_CAPACITY",
    100,
)

if AGENT_CONTEXT_SUMMARIZE_AT_UNITS >= AGENT_CONTEXT_MAX_UNITS:
    raise ConfigurationError("摘要阈值必须小于最大输入预算")

def load_api_key() -> str:
    raw = os.getenv(API_KEY_ENV_NAME)

    if raw is None or not raw.strip():
        raise ConfigurationError(
            f"缺少环境变量: {API_KEY_ENV_NAME}"
        )

    return raw.strip()
