import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import PersistentMessageSidStore, TurnQueueProcessor, TurnTask


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_sid_store_persistence() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = os.path.join(tmpdir, "seen_sids.jsonl")
        store = PersistentMessageSidStore(path=store_path, max_entries=100)

        first = store.seen_or_add("SM123")
        second = store.seen_or_add("SM123")
        assert_true(first is False, "First seen_or_add should return False.")
        assert_true(second is True, "Second seen_or_add should return True.")

        # Reload to verify persistence across process lifecycle.
        reloaded = PersistentMessageSidStore(path=store_path, max_entries=100)
        third = reloaded.seen_or_add("SM123")
        assert_true(third is True, "Reloaded store must detect existing SID.")


def test_turn_queue_basic_processing() -> None:
    processed: list[tuple[str, str]] = []
    sent: list[tuple[str, str, str, str]] = []
    done = threading.Event()

    def process_fn(from_number: str, body: str):
        processed.append((from_number, body))
        return f"echo:{body}", "NEXT_STATE"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        sent.append((to_number, reply_text, post_state, inbound_sid))
        if len(sent) >= 3:
            done.set()

    queue = TurnQueueProcessor(
        worker_count=2,
        max_queue_size=10,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=1,
    )
    queue.start()
    try:
        for idx in range(3):
            ok = queue.submit(
                TurnTask(
                    from_number=f"whatsapp:+9100000000{idx}",
                    body=f"msg-{idx}",
                    inbound_sid=f"SMQ{idx}",
                    pre_state="INIT",
                )
            )
            assert_true(ok, f"Queue rejected task {idx}.")

        assert_true(done.wait(timeout=5), "Queue did not process tasks in time.")
        assert_true(len(processed) == 3, "All tasks should be processed.")
        assert_true(len(sent) == 3, "All processed tasks should be sent.")
    finally:
        queue.stop()


def test_turn_queue_retry() -> None:
    attempts: dict[str, int] = {}
    sent: list[tuple[str, str, str, str]] = []
    done = threading.Event()

    def process_fn(from_number: str, body: str):
        attempts[body] = attempts.get(body, 0) + 1
        if body == "needs-retry" and attempts[body] == 1:
            raise RuntimeError("simulated failure")
        return "ok", "DONE"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        sent.append((to_number, reply_text, post_state, inbound_sid))
        done.set()

    queue = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=5,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,
    )
    queue.start()
    try:
        ok = queue.submit(
            TurnTask(
                from_number="whatsapp:+919999999999",
                body="needs-retry",
                inbound_sid="SMRETRY1",
                pre_state="INIT",
            )
        )
        assert_true(ok, "Queue rejected retry task.")
        assert_true(done.wait(timeout=8), "Retry task was not completed in time.")
        assert_true(attempts.get("needs-retry", 0) == 2, "Retry task should run exactly 2 attempts.")
        assert_true(len(sent) == 1, "Retry-success task should be sent exactly once.")
    finally:
        queue.stop()


def main() -> int:
    tests = [
        ("sid_store_persistence", test_sid_store_persistence),
        ("turn_queue_basic_processing", test_turn_queue_basic_processing),
        ("turn_queue_retry", test_turn_queue_retry),
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"[FAIL] {name}: {exc}")

    print("")
    print(f"Runtime queue/dedup tests: passed={len(tests)-len(failures)} failed={len(failures)} total={len(tests)}")
    if failures:
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

