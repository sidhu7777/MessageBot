import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import TurnQueueProcessor, TurnTask


def test_hard_queue_mix_of_success_retry_timeout_and_permanent_failures() -> None:
    # 50 mixed tasks:
    # - 20 OK first try
    # - 10 timeout once then fail (timeouts are non-retriable in current runtime)
    # - 10 transient error once then succeed
    # - 5 always timeout (final fail)
    # - 5 always error (final fail)
    sent = []
    attempts: dict[str, int] = {}
    timeout_hits = []
    terminal_fails = []
    done = threading.Event()
    total = 50

    def process_fn(from_number: str, body: str):
        idx = int(body.split("-", 1)[1])
        attempts[body] = attempts.get(body, 0) + 1
        turn = attempts[body]
        time.sleep(0.01)

        if idx < 20:
            return "ok", "DONE"
        if idx < 30:
            if turn == 1:
                raise TimeoutError("one-time timeout")
            return "ok", "DONE"
        if idx < 40:
            if turn == 1:
                raise RuntimeError("one-time failure")
            return "ok", "DONE"
        if idx < 45:
            raise TimeoutError("always timeout")
        raise RuntimeError("always fail")

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        sent.append((to_number, inbound_sid))
        if len(sent) >= 30:
            # expected eventual successes
            done.set()

    def timeout_fn(task: TurnTask, exc: Exception):
        timeout_hits.append(task.inbound_sid)

    def on_failure(task: TurnTask, exc: Exception, will_retry: bool, backoff_seconds: float):
        if not will_retry:
            terminal_fails.append(task.inbound_sid)
            if len(terminal_fails) >= 20:
                done.set()

    queue = TurnQueueProcessor(
        worker_count=4,
        max_queue_size=200,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=2,  # total attempts = 3
        timeout_fn=timeout_fn,
        on_failure=on_failure,
    )
    queue.start()
    try:
        for i in range(total):
            ok = queue.submit(
                TurnTask(
                    from_number=f"whatsapp:+91999999{i:04d}",
                    body=f"task-{i}",
                    inbound_sid=f"SID-{i}",
                    pre_state="INIT",
                )
            )
            assert ok, f"Queue rejected task-{i}"

        deadline = time.time() + 20
        while time.time() < deadline:
            snap = queue.snapshot()
            if snap["processed"] + snap["failed"] >= total:
                break
            time.sleep(0.05)

        snap = queue.snapshot()
        assert snap["processed"] == 30, f"processed={snap['processed']}"
        assert snap["failed"] == 20, f"failed={snap['failed']}"
        assert len(sent) == 30
        assert len(terminal_fails) == 20
        # timeout-once (10) + always-timeout (5), each once (no timeout retries)
        assert len(timeout_hits) == 15, f"timeout_hits={len(timeout_hits)}"
        assert done.is_set() or (snap["processed"] + snap["failed"] == total)
    finally:
        queue.stop()
