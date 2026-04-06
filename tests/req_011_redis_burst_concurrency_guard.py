"""
REQ-011: Redis Guard Hard Burst/Concurrency Test
Harder scenario than REQ-010:
  - Simulates many concurrent messages for same user while processing is active
  - Ensures only one turn is accepted, others dropped
  - Ensures busy hint is sent at most once in burst window
  - Ensures another user is not blocked by first user lock
  - Ensures next message is accepted after release

Run:
  python tests/req_011_redis_burst_concurrency_guard.py
"""

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.user_processing_guard import UserProcessingGuard

PASS = 0
FAIL = 0


class ThreadSafeFakeRedis:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[str, float]] = {}

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if exp > 0 and exp <= now]
        for key in expired:
            self._store.pop(key, None)

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            self._cleanup_expired()
            if nx and key in self._store:
                return False
            ttl = float(ex or 0)
            expiry = time.time() + ttl if ttl > 0 else 0.0
            self._store[key] = (str(value), expiry)
            return True

    def delete(self, key):
        with self._lock:
            self._cleanup_expired()
            self._store.pop(key, None)
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


def test_concurrent_burst_same_user() -> None:
    print("\n[TEST 1] 20 concurrent messages for same user")
    redis = ThreadSafeFakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=20, busy_ttl_seconds=5, key_prefix="hard")

    user = "telegram:burst-user"
    accepted = 0
    dropped = 0
    busy_hint_allowed = 0

    counter_lock = threading.Lock()
    start_gate = threading.Barrier(20)

    def worker() -> None:
        nonlocal accepted, dropped, busy_hint_allowed
        start_gate.wait()
        ok = guard.acquire(user)
        if ok:
            with counter_lock:
                accepted += 1
            # Simulate active processing window.
            time.sleep(0.06)
            guard.release(user)
        else:
            with counter_lock:
                dropped += 1
            if guard.allow_busy_hint(user):
                with counter_lock:
                    busy_hint_allowed += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("Exactly one message accepted in burst", accepted == 1, f"accepted={accepted}")
    check("Remaining messages dropped", dropped == 19, f"dropped={dropped}")
    check("Busy hint sent at most once in burst", busy_hint_allowed <= 1, f"busy_hint_allowed={busy_hint_allowed}")


def test_second_user_not_blocked() -> None:
    print("\n[TEST 2] User B is not blocked while User A is processing")
    redis = ThreadSafeFakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=20, busy_ttl_seconds=5, key_prefix="hard")

    user_a = "telegram:user-a"
    user_b = "telegram:user-b"

    a_first = guard.acquire(user_a)
    a_second = guard.acquire(user_a)
    b_first = guard.acquire(user_b)

    check("User A first acquire succeeds", a_first is True)
    check("User A second acquire blocked", a_second is False)
    check("User B acquire succeeds independently", b_first is True)

    guard.release(user_a)
    guard.release(user_b)


def test_accept_after_release_in_next_turn() -> None:
    print("\n[TEST 3] Next message is accepted after processing release")
    redis = ThreadSafeFakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=20, busy_ttl_seconds=2, key_prefix="hard")

    user = "telegram:next-turn"

    first = guard.acquire(user)
    blocked = guard.acquire(user)
    guard.release(user)
    next_turn = guard.acquire(user)

    check("First acquire succeeds", first is True)
    check("Immediate second blocked", blocked is False)
    check("Next turn accepted after release", next_turn is True)


def test_busy_hint_window_expires() -> None:
    print("\n[TEST 4] Busy hint can be shown again after TTL window")
    redis = ThreadSafeFakeRedis()
    guard = UserProcessingGuard(redis_client=redis, lock_ttl_seconds=20, busy_ttl_seconds=1, key_prefix="hard")

    user = "telegram:busy-window"

    first_hint = guard.allow_busy_hint(user)
    second_hint = guard.allow_busy_hint(user)
    # Guard enforces a minimum busy hint TTL of 2 seconds.
    time.sleep(2.2)
    third_hint = guard.allow_busy_hint(user)

    check("First hint allowed", first_hint is True)
    check("Second hint blocked in same window", second_hint is False)
    check("Hint allowed again after TTL", third_hint is True)


if __name__ == "__main__":
    print("=" * 68)
    print("REQ-011: Redis Guard Hard Burst/Concurrency Test")
    print("=" * 68)

    test_concurrent_burst_same_user()
    test_second_user_not_blocked()
    test_accept_after_release_in_next_turn()
    test_busy_hint_window_expires()

    print("\n" + "=" * 68)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 68)
    sys.exit(0 if FAIL == 0 else 1)
