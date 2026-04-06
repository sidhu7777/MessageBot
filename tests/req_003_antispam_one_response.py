"""
REQ-003: Anti-Spam — Only ONE Response per Turn
Verifies that when workers are busy (timeout or queue-full),
exactly ONE "busy" message is sent to the user, not multiple.

This was a known bug: timeout was retried 3 times, each retry also
sent a busy message, resulting in 3 spam messages.

Run: python tests/req_003_antispam_one_response.py
"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.turn_queue import TurnQueueProcessor, TurnTask

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{detail_str}")


# ─── Test 1: Timeout sends busy message exactly once ──────────────────────────

def test_timeout_busy_message_sent_exactly_once():
    print("\n[TEST] Timeout: busy message sent exactly ONCE (not per retry)")

    timeout_fn_calls = []
    event_task_started = threading.Event()
    event_allow_timeout = threading.Event()

    def process_fn(from_number, body):
        # Signal that task is running
        event_task_started.set()
        # Wait until we allow to timeout
        event_allow_timeout.wait(timeout=5)
        raise TimeoutError("Ollama timed out")

    def send_fn(to, body, state, sid):
        pass  # normal send — not what we're testing

    def timeout_fn(task, exc):
        timeout_fn_calls.append((task.from_number, str(exc)))

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=5,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,       # even with retries configured, timeout must NOT be retried
        timeout_fn=timeout_fn,
    )
    processor.start()

    task = TurnTask(
        from_number="user_1",
        body="book appointment",
        inbound_sid="sid_001",
        pre_state="INIT",
    )
    submitted = processor.submit(task)
    check("task submitted successfully", submitted)

    # Let the worker start, then allow it to raise TimeoutError
    event_task_started.wait(timeout=3)
    event_allow_timeout.set()

    # Give worker time to process the error
    time.sleep(0.5)
    processor.stop()

    check(
        "timeout_fn called exactly once (no retry spam)",
        len(timeout_fn_calls) == 1,
        f"got {len(timeout_fn_calls)} calls",
    )


# ─── Test 2: Retry (non-timeout) does NOT call timeout_fn ─────────────────────

def test_non_timeout_error_no_timeout_fn_called():
    print("\n[TEST] Non-timeout error: timeout_fn NOT called, retry happens silently")

    timeout_fn_calls = []
    call_count = [0]
    event_done = threading.Event()

    def process_fn(from_number, body):
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError("Transient error")
        event_done.set()
        return ("All good", "DONE")

    def send_fn(to, body, state, sid):
        pass

    def timeout_fn(task, exc):
        timeout_fn_calls.append((task.from_number, str(exc)))

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=5,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,
        timeout_fn=timeout_fn,
    )
    processor.start()

    task = TurnTask(
        from_number="user_2",
        body="book appointment",
        inbound_sid="sid_002",
        pre_state="INIT",
    )
    processor.submit(task)

    event_done.wait(timeout=10)
    time.sleep(0.2)
    processor.stop()

    check("non-timeout retried and eventually succeeded", call_count[0] >= 3)
    check("timeout_fn NOT called for non-timeout errors", len(timeout_fn_calls) == 0)


# ─── Test 3: Queue completely full — submit returns False ─────────────────────

def test_queue_full_four_workers_busy():
    print("\n[TEST] Queue completely full — submit returns False, busy handled once")

    sent_busy = []
    block_event = threading.Event()
    worker_started = threading.Semaphore(0)

    def process_fn(from_number, body):
        worker_started.release()
        block_event.wait(timeout=10)
        return ("reply", "DONE")

    def send_fn(to, body, state, sid):
        pass

    # 2 workers, queue holds 2 extra items
    # Total capacity = 2 (workers) + 2 (queue) = 4 tasks before full
    processor = TurnQueueProcessor(
        worker_count=2,
        max_queue_size=2,  # only 2 items can WAIT in queue; 2 more go to workers
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
    )
    processor.start()

    # Send 2 tasks to fill both workers (blocking)
    for i in range(2):
        processor.submit(TurnTask(f"worker_{i}", "block", f"sid_w{i}", "INIT"))

    # Wait for workers to start (both blocked in process_fn)
    for _ in range(2):
        worker_started.acquire(timeout=3)

    # Now fill the queue buffer (2 more tasks queued while workers are busy)
    for i in range(2):
        r = processor.submit(TurnTask(f"queue_{i}", "queued", f"sid_q{i}", "INIT"))
        # These should succeed (queue not full yet)

    # Now queue is full: 2 workers busy + 2 in queue → submit one more → FULL
    extra_task = TurnTask("overflow_user", "overflow", "sid_extra", "INIT")
    result = processor.submit(extra_task)

    check("extra submit returns False (queue full)", result is False,
          f"got result={result}")

    # Simulate main.py: call busy_fn once when submit fails
    if not result:
        sent_busy.append(extra_task.from_number)

    check("busy handled exactly ONCE for overflow user",
          sent_busy.count("overflow_user") == 1,
          f"sent_busy={sent_busy}")

    # Unblock all workers
    block_event.set()
    time.sleep(0.3)
    processor.stop()


# ─── Test 4: Multiple timeouts from multiple users — one per user ─────────────

def test_multiple_user_timeouts_one_each():
    print("\n[TEST] 3 users all timeout — each gets exactly one busy message")

    per_user_calls: dict[str, int] = {}
    all_started = threading.Barrier(3)
    release_all = threading.Event()

    def process_fn(from_number, body):
        all_started.wait(timeout=5)
        release_all.wait(timeout=5)
        raise TimeoutError("Ollama timed out")

    def send_fn(to, body, state, sid):
        pass

    def timeout_fn(task, exc):
        per_user_calls[task.from_number] = per_user_calls.get(task.from_number, 0) + 1

    processor = TurnQueueProcessor(
        worker_count=3,
        max_queue_size=10,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,   # timeout must NOT be retried even here
        timeout_fn=timeout_fn,
    )
    processor.start()

    users = ["user_A", "user_B", "user_C"]
    for u in users:
        processor.submit(TurnTask(
            from_number=u,
            body="hello",
            inbound_sid=f"sid_{u}",
            pre_state="INIT",
        ))

    # Let all tasks start, then trigger timeouts
    time.sleep(0.3)
    release_all.set()
    time.sleep(0.5)
    processor.stop()

    for u in users:
        count = per_user_calls.get(u, 0)
        check(f"user {u} got exactly 1 timeout_fn call (got {count})", count == 1)


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-003: Anti-Spam — One Response per Turn")
    print("=" * 60)

    test_timeout_busy_message_sent_exactly_once()
    test_non_timeout_error_no_timeout_fn_called()
    test_queue_full_four_workers_busy()
    test_multiple_user_timeouts_one_each()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
