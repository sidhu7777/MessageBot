import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import TurnQueueProcessor, TurnTask


def test_timeout_hook_invoked_and_final_failure_recorded() -> None:
    seen = {"timeout": 0, "failure": 0}
    done = threading.Event()

    def process_fn(from_number: str, body: str):
        raise TimeoutError("simulated timeout")

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        return None

    def timeout_fn(task: TurnTask, exc: Exception):
        seen["timeout"] += 1

    def on_failure(task: TurnTask, exc: Exception, will_retry: bool, backoff: float):
        if not will_retry:
            seen["failure"] += 1
            done.set()

    queue = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=5,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
        timeout_fn=timeout_fn,
        on_failure=on_failure,
    )
    queue.start()
    try:
        ok = queue.submit(
            TurnTask(
                from_number="whatsapp:+919999999999",
                body="hello",
                inbound_sid="SM-TIMEOUT",
                pre_state="INIT",
            )
        )
        assert ok
        assert done.wait(timeout=5)
        assert seen["timeout"] >= 1
        assert seen["failure"] >= 1
    finally:
        queue.stop()

