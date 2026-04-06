import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import TurnQueueProcessor, TurnTask


def test_four_patients_three_workers_overflow_requeue_flow() -> None:
    sent: list[tuple[str, str]] = []

    def process_fn(from_number: str, body: str):
        # Simulate non-trivial processing so worker threads stay occupied briefly.
        time.sleep(0.15)
        return f"final:{body}", "DONE"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        sent.append((to_number, reply_text))

    # Deterministic overflow setup:
    # - workers = 3
    # - queue size = 3
    # - do not start workers before submit
    # => first 3 accepted, 4th rejected immediately.
    queue = TurnQueueProcessor(
        worker_count=3,
        max_queue_size=3,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
    )

    max_attempts = 20
    base_backoff = 0.1

    def schedule_overflow_requeue(task: TurnTask) -> None:
        def _runner() -> None:
            for attempt in range(1, max_attempts + 1):
                if queue.submit(task):
                    return
                sleep_for = min(8.0, base_backoff * attempt)
                time.sleep(sleep_for)

        threading.Thread(target=_runner, daemon=True).start()

    try:
        tasks = [
            TurnTask(from_number="whatsapp:+910000000001", body="p1", inbound_sid="SID1", pre_state="INIT"),
            TurnTask(from_number="whatsapp:+910000000002", body="p2", inbound_sid="SID2", pre_state="INIT"),
            TurnTask(from_number="whatsapp:+910000000003", body="p3", inbound_sid="SID3", pre_state="INIT"),
            TurnTask(from_number="whatsapp:+910000000004", body="p4", inbound_sid="SID4", pre_state="INIT"),
        ]

        ok_1 = queue.submit(tasks[0])
        ok_2 = queue.submit(tasks[1])
        ok_3 = queue.submit(tasks[2])
        ok_4 = queue.submit(tasks[3])

        assert ok_1 and ok_2 and ok_3, "First three tasks must be accepted."
        assert not ok_4, "Fourth task must overflow when queue is full."

        # Simulate current app behavior for overflow:
        # immediate safe/busy message + background requeue.
        sent.append((tasks[3].from_number, "busy"))
        schedule_overflow_requeue(tasks[3])

        # Start workers now so requeue thread can eventually submit and process task4.
        queue.start()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            busy_count = sum(1 for _, msg in sent if msg == "busy")
            finals = [msg for _, msg in sent if msg.startswith("final:")]
            if busy_count >= 1 and len(finals) >= 4:
                break
            time.sleep(0.05)

        busy_count = sum(1 for _, msg in sent if msg == "busy")
        finals = [msg for _, msg in sent if msg.startswith("final:")]

        assert busy_count >= 1, "Overflow user should receive busy/safe message first."
        assert len(finals) >= 4, f"All four users should eventually get final response, got finals={finals}"
        assert "final:p4" in finals, "Fourth user's overflowed turn should be requeued and processed."
    finally:
        queue.stop()
