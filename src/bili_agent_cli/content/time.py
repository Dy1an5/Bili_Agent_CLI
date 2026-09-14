from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from pydantic import PlainSerializer


BEIJING_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")
BEIJING_TIME_FORMAT = "%Y-%m-%d %H:%M (UTC+8)"


def format_beijing_time(value: datetime | None) -> str | None:
    """将时间转换为供 Agent 阅读的北京时间文本。"""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TIMEZONE).strftime(BEIJING_TIME_FORMAT)


BeijingTime = Annotated[
    datetime,
    PlainSerializer(
        format_beijing_time,
        return_type=str,
        when_used="json",
    ),
]
