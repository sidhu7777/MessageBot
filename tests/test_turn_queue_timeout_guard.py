import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.turn_queue import TurnQueueProcessor, TurnTask


def test_timeout_message_suppressed_when_real_send_starts_soon_after_timeout() -> None:
    sends = []
    timeout_calls = []

    def process_fn(_from: str, _body: str):
        time.sleep(0.22)
        return ("real reply", "INIT")

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        sends.append((to_number, reply_text, post_state, inbound_sid))

    def timeout_fn(task: TurnTask, exc: Exception):
        timeout_calls.append((task.from_number, str(exc)))

    processor = TurnQueueProcessor(
        worker_count=1,
        max_queue_size=5,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=0,
        processing_timeout_seconds=0.20,
        timeout_fn=timeout_fn,
    )
    processor.start()
    try:
        assert processor.submit(TurnTask("telegram:1", "hello", "sid1", "INIT")) is True
        deadline = time.time() + 3.0
        while time.time() < deadline and not sends:
            time.sleep(0.02)
    finally:
        processor.stop()

    assert len(sends) == 1
    assert timeout_calls == []
