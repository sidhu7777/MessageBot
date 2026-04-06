"""
REQ-016: Processing Guard Non-Blocking Fallback
Validates that slow/unavailable Redis does not block lock/busy operations.

Run:
  python tests/req_016_processing_guard_non_blocking_fallback.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.user_processing_guard import UserProcessingGuard

PASS = 0
FAIL = 0


class SlowRedis:
    def __init__(self, sleep_seconds: float = 0.25) -> None:
        self.sleep_seconds = sleep_seconds
        self.set_calls = 0
        self.delete_calls = 0

    def set(self, key, value, nx=False, ex=None):
        self.set_calls += 1
        time.sleep(self.sleep_seconds)
        return True

    def delete(self, key):
        self.delete_calls += 1
        time.sleep(self.sleep_seconds)
        return 1


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


def test_fail_open_and_cooldown() -> None:
    print("\n[TEST 1] fail-open on slow Redis + cooldown short-circuit")
    redis = SlowRedis(sleep_seconds=0.25)
    guard = UserProcessingGuard(
        redis_client=redis,
        lock_ttl_seconds=30,
        busy_ttl_seconds=5,
        key_prefix="nb",
        redis_op_timeout_ms=30,
        fail_open_cooldown_seconds=1.0,
    )

    start = time.perf_counter()
    first = guard.acquire("telegram:slow-user")
    first_elapsed = time.perf_counter() - start
    check("Acquire fail-open returns True", first is True)
    check("Acquire returns quickly on slow Redis", first_elapsed < 0.12, f"elapsed={first_elapsed:.3f}s")
    check("First call touched Redis exactly once", redis.set_calls == 1, f"set_calls={redis.set_calls}")

    start = time.perf_counter()
    second = guard.acquire("telegram:slow-user")
    second_elapsed = time.perf_counter() - start
    check("Second acquire during cooldown also returns True", second is True)
    check("Cooldown short-circuits immediately", second_elapsed < 0.03, f"elapsed={second_elapsed:.3f}s")
    check("Second call skipped Redis due to cooldown", redis.set_calls == 1, f"set_calls={redis.set_calls}")

    start = time.perf_counter()
    busy = guard.allow_busy_hint("telegram:slow-user")
    busy_elapsed = time.perf_counter() - start
    check("Busy hint fallback returns False", busy is False)
    check("Busy hint also short-circuits during cooldown", busy_elapsed < 0.03, f"elapsed={busy_elapsed:.3f}s")
    check("Busy hint did not hit Redis during cooldown", redis.set_calls == 1, f"set_calls={redis.set_calls}")

    time.sleep(1.1)
    start = time.perf_counter()
    third = guard.acquire("telegram:slow-user")
    third_elapsed = time.perf_counter() - start
    check("Acquire after cooldown still fail-open True", third is True)
    check("Post-cooldown slow Redis still bounded by timeout", third_elapsed < 0.12, f"elapsed={third_elapsed:.3f}s")
    check("Redis retried after cooldown", redis.set_calls == 2, f"set_calls={redis.set_calls}")


if __name__ == "__main__":
    print("=" * 68)
    print("REQ-016: Processing Guard Non-Blocking Fallback")
    print("=" * 68)

    test_fail_open_and_cooldown()

    print("\n" + "=" * 68)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 68)
    sys.exit(0 if FAIL == 0 else 1)
