import os
from datetime import datetime
from zoneinfo import ZoneInfo


def get_runtime_timezone() -> ZoneInfo:
    tz_name = (os.getenv("LOG_TIMEZONE") or os.getenv("TZ") or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def now_in_runtime_timezone() -> datetime:
    return datetime.now(get_runtime_timezone())
