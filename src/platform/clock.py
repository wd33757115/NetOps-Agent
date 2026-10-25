"""Beijing time for every timestamp people and the model read.

Mixed offsets were once read as one moment (a baseline at 17:55+08:00 and a live read
at 12:55 UTC were called simultaneous); one zone everywhere ends that.
"""

import re
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
_ZONED = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def beijing(value):
    """A zoned moment as Beijing-time ISO text; anything else is returned unchanged."""
    if isinstance(value, datetime):
        return value.astimezone(BEIJING).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, str) and _ZONED.fullmatch(value):
        return datetime.fromisoformat(value).astimezone(BEIJING).isoformat()
    return value


def in_beijing(value):
    """Every zoned timestamp field in a tool result; free text and device output untouched."""
    if isinstance(value, dict):
        return {key: in_beijing(item) for key, item in value.items()}
    if isinstance(value, list):
        return [in_beijing(item) for item in value]
    return beijing(value)
