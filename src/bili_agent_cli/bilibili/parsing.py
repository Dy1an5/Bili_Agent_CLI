from __future__ import annotations

from typing import Any


DEFAULT_AVATAR_URL = "https://i0.hdslb.com/bfs/face/member/noface.jpg"


def as_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    return value


def read_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    return text or None


def read_positive_id(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)

    if isinstance(value, str) and value.strip().isdigit():
        identifier = value.strip()
        return identifier if int(identifier) > 0 else None

    return None


def read_non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value

    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())

    return default


def read_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass

    return default


def read_positive_timestamp(value: Any) -> int | None:
    timestamp = read_non_negative_int(value)
    return timestamp if timestamp > 0 else None


def normalize_image_url(value: Any) -> str | None:
    url = read_non_empty_string(value)

    if url is None:
        return None

    if url.startswith("//"):
        return f"https:{url}"

    if url.startswith("http://"):
        return f"https://{url.removeprefix('http://')}"

    return url
