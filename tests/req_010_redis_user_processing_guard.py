"""
REQ-010: Redis User Processing Guard
Validates per-user lock/drop semantics used to prevent duplicate replies for rapid
multi-message bursts like: hi, hi, hi.

Run:
  python tests/req_010_redis_user_processing_guard.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.user_processing_guard import UserProcessingGuard

PASS = 0
FAIL = 0


class FakeRedis:
    def __init__(self) -> None:
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = (value, ex)
        return True

    def delete(self, key):
        self.store.pop(key, None)
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


def test_acquire_drop_release_cycle() -> None:
    print("\n[TEST 1] acquire -> drop -> release -> acquire")
    redis = FakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=30, busy_ttl_seconds=5, key_prefix="t")

    user = "telegram:123"
    first = guard.acquire(user)
    second = guard.acquire(user)
    guard.release(user)
    third = guard.acquire(user)

    check("First acquire succeeds", first is True)
    check("Second acquire blocked while lock held", second is False)
    check("Acquire succeeds again after release", third is True)


def test_busy_hint_throttle() -> None:
    print("\n[TEST 2] busy hint throttles to one")
    redis = FakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=30, busy_ttl_seconds=5, key_prefix="t")

    user = "telegram:999"
    first_hint = guard.allow_busy_hint(user)
    second_hint = guard.allow_busy_hint(user)

    check("First busy hint allowed", first_hint is True)
    check("Second busy hint suppressed in same window", second_hint is False)


def test_no_redis_fallback() -> None:
    print("\n[TEST 3] no-redis fallback is safe")
    guard = UserProcessingGuard(redis_client=None)

    user = "telegram:444"
    check("Acquire returns True without redis", guard.acquire(user) is True)
    check("Busy hint disabled without redis", guard.allow_busy_hint(user) is False)


def test_multi_user_isolated_locks() -> None:
    print("\n[TEST 4] locks are isolated per user")
    redis = FakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=30, busy_ttl_seconds=5, key_prefix="t")

    u1 = "telegram:1"
    u2 = "telegram:2"
    check("User1 acquire succeeds", guard.acquire(u1) is True)
    check("User2 acquire succeeds independently", guard.acquire(u2) is True)
    check("User1 second acquire blocked", guard.acquire(u1) is False)
    check("User2 second acquire blocked", guard.acquire(u2) is False)


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-010: Redis User Processing Guard")
    print("=" * 60)

    test_acquire_drop_release_cycle()
    test_busy_hint_throttle()
    test_no_redis_fallback()
    test_multi_user_isolated_locks()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
