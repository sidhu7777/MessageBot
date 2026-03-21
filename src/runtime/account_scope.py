from __future__ import annotations

from typing import Optional, Tuple


def build_scoped_user_id(channel_account_id: int, raw_user_id: str) -> str:
    return f"acct:{int(channel_account_id)}|{(raw_user_id or '').strip()}"


def parse_scoped_user_id(value: str) -> Tuple[Optional[int], str]:
    raw = (value or "").strip()
    if not raw.startswith("acct:"):
        return None, raw
    prefix, _, rest = raw.partition("|")
    account_part = prefix[len("acct:") :].strip()
    try:
        account_id = int(account_part)
    except Exception:
        return None, raw
    return account_id, rest.strip()
