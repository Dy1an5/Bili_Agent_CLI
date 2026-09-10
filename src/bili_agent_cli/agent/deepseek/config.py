import os

from .errors import ConfigurationError

API_KEY_ENV_NAME = "DEEPSEEK_API_KEY"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

def load_api_key() -> str:
    raw = os.getenv(API_KEY_ENV_NAME)

    if raw is None or not raw.strip():
        raise ConfigurationError(
            f"缺少环境变量: {API_KEY_ENV_NAME}"
        )

    return raw.strip()