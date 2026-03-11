"""
Per-chat per-day structured file logger.

Creates:
    logs/
      YYYY-MM-DD/
        chat_<chat_id>.log    â† one file per user per day

Each line format:
    [HH:MM:SS.mmm] EVENT_NAME            key=value | key=value

Thread-safe, line-buffered writes. Never raises â€” all exceptions silently swallowed
so a logging failure can never crash the bot.

Usage (anywhere in the codebase):
    from src.chat_logger import log_event, extract_chat_id

    chat_id = extract_chat_id(from_number)   # strips "telegram:" prefix
    log_event(chat_id, "WEBHOOK_ARRIVED", sid=inbound_sid, text=text[:80])
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Root log directory â€” project_root/logs/
_LOGS_ROOT = Path(__file__).resolve().parent.parent / "logs"

# Open file handles keyed by "YYYY-MM-DD:chat_id".
# Line-buffered so each write is immediately flushed to disk.
_file_handles: dict[str, object] = {}
_handles_lock = threading.Lock()

# Evict oldest handles if we accumulate too many open files.
_MAX_OPEN_FILES = 300


# â”€â”€â”€ Internal helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _safe_chat_id(chat_id: str) -> str:
    """Return a filesystem-safe version of chat_id."""
    return "".join(
        c if (c.isalnum() or c in ("-", "_")) else "_"
        for c in str(chat_id or "unknown")
    )


def _get_file_handle(chat_id: str):
    """Return (or open) the line-buffered log file for this chat_id + today."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    key = f"{date_str}:{chat_id}"

    with _handles_lock:
        fh = _file_handles.get(key)
        if fh is not None:
            return fh

        # Evict oldest when too many handles are open.
        if len(_file_handles) >= _MAX_OPEN_FILES:
            oldest_key = next(iter(_file_handles))
            try:
                _file_handles.pop(oldest_key).close()
            except Exception:
                pass

        try:
            day_dir = _LOGS_ROOT / date_str
            day_dir.mkdir(parents=True, exist_ok=True)
            log_path = day_dir / f"chat_{_safe_chat_id(chat_id)}.log"
            fh = open(log_path, "a", encoding="utf-8", buffering=1)  # line-buffered
            _file_handles[key] = fh
            return fh
        except Exception:
            return None


# â”€â”€â”€ Public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def extract_chat_id(from_number: str) -> str:
    """
    Extract the bare chat identifier from a from_number string.

    Examples:
        "telegram:123456789"  â†’  "123456789"
        "+919876543210"       â†’  "+919876543210"
        ""                    â†’  "unknown"
    """
    s = (from_number or "").strip()
    if s.lower().startswith("telegram:"):
        return s[len("telegram:"):]
    return s or "unknown"


def log_event(chat_id: str, event: str, **kwargs) -> None:
    """
    Write one structured log line to logs/YYYY-MM-DD/chat_<chat_id>.log.

    Args:
        chat_id: bare chat id (not prefixed with "telegram:")
        event:   short ALL_CAPS event name, e.g. "WEBHOOK_ARRIVED"
        **kwargs: arbitrary key=value pairs included in the log line

    Example output:
        [14:32:01.123] WEBHOOK_ARRIVED           sid='42' text='hello'
        [14:32:01.156] LOCK_ACQUIRED             ms=32
        [14:32:01.158] SESSION_REDIS_HIT         load_ms=2
        [14:32:01.201] FSM_HANDLED               pre='INIT' post='ASK_BOOKING_FOR' fsm_ms=41
        [14:32:01.210] BOT_REPLY_SENT            send_ms=35 reply='Welcome! How can I help...'
        [14:32:01.246] TURN_END                  total_ms=122 save_ms=8
    """
    try:
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm
        parts = []
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, str):
                # Truncate long strings; display without outer quotes for readability
                v_display = v[:120].replace("\n", " ").replace("\r", "")
            else:
                v_display = str(v)
            parts.append(f"{k}={v_display!r}" if isinstance(v, str) else f"{k}={v_display}")
        detail = "  ".join(parts)
        line = f"[{now}]  {event:<32}  {detail}\n"
        fh = _get_file_handle(str(chat_id))
        if fh is not None:
            fh.write(line)
    except Exception:
        # Logging must never crash the bot.
        pass
