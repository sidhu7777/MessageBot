"""
REQ-004: App Crash / Worker Auto-Recovery
Verifies that when a worker task crashes (any exception),
the worker thread stays alive and continues processing next tasks.

Logic: _worker_loop catches all exceptions in _run_task.
       Workers never die due to a bad task.

Run: python tests/req_004_crash_auto_recovery.py
"""
import sys
import threading
import time
from pathlib import Path

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


# ─── Test 1: Worker survives a crashing task and processes next ────────────────

def test_worker_survives_runtime_error():
    print("\n[TEST] Worker survives RuntimeError and processes next task")

    processed = []
    event = threading.Event()

    def process_fn(from_number, body):
        if body == "CRASH":
            raise RuntimeError("Simulated crash in process_fn")
        processed.append(from_number)
        if len(processed) >= 1:
            event.set()
        return ("ok", "DONE")

    def send_fn(to, body, state, sid):
        pass

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=10,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,  # no retries: test pure crash recovery
    )
    processor.start()

    # Send a crashing task first
    processor.submit(TurnTask("user_crash", "CRASH", "sid_crash", "INIT"))

    # Small pause to let the crash happen
    time.sleep(0.3)

    # Check worker is still alive
    alive_after_crash = sum(1 for t in processor._threads if t.is_alive())
    check("worker thread still alive after crash", alive_after_crash >= 1,
          f"alive={alive_after_crash}/{processor.worker_count}")

    # Now send a good task — it must be processed
    processor.submit(TurnTask("user_ok", "hello", "sid_ok", "INIT"))
    event.wait(timeout=5)

    time.sleep(0.2)
    processor.stop()

    check("good task processed after crash", "user_ok" in processed)


# ─── Test 2: Worker survives ValueError ───────────────────────────────────────

def test_worker_survives_value_error():
    print("\n[TEST] Worker survives ValueError and keeps all workers alive")

    processed_ok = []
    event = threading.Event()

    def process_fn(from_number, body):
        if body.startswith("BAD"):
            raise ValueError(f"Bad input: {body}")
        processed_ok.append(from_number)
        if len(processed_ok) >= 3:
            event.set()
        return ("reply", "DONE")

    def send_fn(to, body, state, sid):
        pass

    processor = TurnQueueProcessor(
        worker_count=2,
        max_queue_size=20,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
    )
    processor.start()

    # Mix: crash, ok, crash, ok, ok
    tasks = [
        ("user_bad1", "BAD_input_1"),
        ("user_ok1", "hello 1"),
        ("user_bad2", "BAD_input_2"),
        ("user_ok2", "hello 2"),
        ("user_ok3", "hello 3"),
    ]
    for from_num, body in tasks:
        processor.submit(TurnTask(from_num, body, f"sid_{from_num}", "INIT"))

    event.wait(timeout=8)
    time.sleep(0.3)

    alive = sum(1 for t in processor._threads if t.is_alive())
    processor.stop()

    check("all workers still alive after multiple crashes", alive == 2, f"alive={alive}")
    check("all 3 good tasks processed", len(processed_ok) == 3,
          f"processed: {processed_ok}")


# ─── Test 3: TimeoutError does not kill worker ─────────────────────────────────

def test_worker_survives_timeout():
    print("\n[TEST] Worker survives TimeoutError and processes next task")

    processed = []
    event = threading.Event()

    def process_fn(from_number, body):
        if body == "TIMEOUT":
            raise TimeoutError("Ollama timed out")
        processed.append(from_number)
        event.set()
        return ("reply", "DONE")

    def send_fn(to, body, state, sid):
        pass

    timeout_called = []

    def timeout_fn(task, exc):
        timeout_called.append(task.from_number)

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=10,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,
        timeout_fn=timeout_fn,
    )
    processor.start()

    processor.submit(TurnTask("user_timeout", "TIMEOUT", "sid_t", "INIT"))
    time.sleep(0.3)

    alive_after = sum(1 for t in processor._threads if t.is_alive())
    check("worker alive after TimeoutError", alive_after >= 1)

    processor.submit(TurnTask("user_after_timeout", "ok", "sid_after", "INIT"))
    event.wait(timeout=5)
    time.sleep(0.2)
    processor.stop()

    check("task after timeout is processed", "user_after_timeout" in processed)
    check("timeout_fn called once", len(timeout_called) == 1,
          f"timeout_called={timeout_called}")


# ─── Test 4: Worker count remains stable over many crashes ────────────────────

def test_worker_count_stable_after_many_crashes():
    print("\n[TEST] All 4 workers remain alive after 20 crashing tasks")

    good_count = [0]
    done_event = threading.Event()

    def process_fn(from_number, body):
        if "crash" in body:
            raise Exception("Intentional crash")
        good_count[0] += 1
        if good_count[0] >= 5:
            done_event.set()
        return ("ok", "DONE")

    def send_fn(to, body, state, sid):
        pass

    processor = TurnQueueProcessor(
        worker_count=4,
        max_queue_size=50,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
    )
    processor.start()

    # 20 crash tasks
    for i in range(20):
        processor.submit(TurnTask(f"crash_user_{i}", f"crash_{i}", f"sid_c{i}", "INIT"))

    time.sleep(0.5)

    # 5 good tasks
    for i in range(5):
        processor.submit(TurnTask(f"good_user_{i}", f"hello_{i}", f"sid_g{i}", "INIT"))

    done_event.wait(timeout=10)
    time.sleep(0.3)

    alive = sum(1 for t in processor._threads if t.is_alive())
    processor.stop()

    check(f"all 4 workers still alive (alive={alive})", alive == 4)
    check("5 good tasks processed after 20 crashes", good_count[0] >= 5,
          f"processed={good_count[0]}")


# ─── Test 5: Worker metrics count failures ─────────────────────────────────────

def test_failure_metrics_tracked():
    print("\n[TEST] Failed task count tracked in processor metrics")

    done = threading.Event()

    def process_fn(from_number, body):
        if body == "fail":
            raise RuntimeError("fail")
        done.set()
        return ("ok", "DONE")

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=10,
        process_fn=process_fn,
        send_fn=lambda *a: None,
        retry_attempts=0,
    )
    processor.start()

    # 3 failures
    for i in range(3):
        processor.submit(TurnTask(f"u{i}", "fail", f"s{i}", "INIT"))

    # 1 success
    processor.submit(TurnTask("ok_user", "ok", "ok_sid", "INIT"))
    done.wait(timeout=5)
    time.sleep(0.2)
    processor.stop()

    snapshot = processor.snapshot()
    check("_failed metric == 3", snapshot["failed"] == 3, f"snapshot={snapshot}")
    check("_processed metric >= 1", snapshot["processed"] >= 1, f"snapshot={snapshot}")


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-004: App Crash / Worker Auto-Recovery")
    print("=" * 60)

    test_worker_survives_runtime_error()
    test_worker_survives_value_error()
    test_worker_survives_timeout()
    test_worker_count_stable_after_many_crashes()
    test_failure_metrics_tracked()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
