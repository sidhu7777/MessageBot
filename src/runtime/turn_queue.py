import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


LOGGER = logging.getLogger(__name__)


@dataclass
class TurnTask:
    from_number: str
    body: str
    inbound_sid: str
    pre_state: str
    attempt: int = 0


class TurnQueueProcessor:
    def __init__(
        self,
        *,
        worker_count: int,
        max_queue_size: int,
        process_fn: Callable[[str, str], Tuple[str, str]],
        send_fn: Callable[[str, str, str, str], None],
        retry_attempts: int = 1,
    ) -> None:
        self.worker_count = worker_count
        self.retry_attempts = max(0, retry_attempts)
        self._process_fn = process_fn
        self._send_fn = send_fn
        self._queue: queue.Queue[Optional[TurnTask]] = queue.Queue(maxsize=max_queue_size)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._metrics_lock = threading.Lock()
        self._submitted = 0
        self._dropped = 0
        self._processed = 0
        self._retried = 0
        self._failed = 0

    def start(self) -> None:
        if self._threads:
            return
        for idx in range(self.worker_count):
            thread = threading.Thread(target=self._worker_loop, name=f"turn-worker-{idx+1}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._threads:
            try:
                self._queue.put_nowait(None)
            except Exception:
                break
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()

    def submit(self, task: TurnTask) -> bool:
        try:
            self._queue.put_nowait(task)
            with self._metrics_lock:
                self._submitted += 1
            return True
        except queue.Full:
            with self._metrics_lock:
                self._dropped += 1
            return False

    def backlog_size(self) -> int:
        return self._queue.qsize()

    def snapshot(self) -> dict:
        with self._metrics_lock:
            return {
                "worker_count": self.worker_count,
                "alive_workers": sum(1 for t in self._threads if t.is_alive()),
                "backlog_size": self._queue.qsize(),
                "submitted": self._submitted,
                "dropped": self._dropped,
                "processed": self._processed,
                "retried": self._retried,
                "failed": self._failed,
                "retry_attempts": self.retry_attempts,
            }

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            task = self._queue.get()
            try:
                if task is None:
                    return
                self._run_task(task)
            finally:
                self._queue.task_done()

    def _run_task(self, task: TurnTask) -> None:
        try:
            reply, post_state = self._process_fn(task.from_number, task.body)
            self._send_fn(task.from_number, reply, post_state, task.inbound_sid)
            with self._metrics_lock:
                self._processed += 1
            LOGGER.info(
                "Queued turn processed sid=%s from=%s state=%s->%s chars=%d",
                task.inbound_sid or "-",
                task.from_number,
                task.pre_state,
                post_state,
                len(reply),
            )
        except Exception as exc:
            if task.attempt < self.retry_attempts and not self._stop.is_set():
                backoff_seconds = min(4.0, 0.8 * (2 ** task.attempt))
                LOGGER.warning(
                    "Queued turn failed; retrying sid=%s from=%s attempt=%d/%d after %.1fs error=%s",
                    task.inbound_sid or "-",
                    task.from_number,
                    task.attempt + 1,
                    self.retry_attempts + 1,
                    backoff_seconds,
                    exc,
                )
                time.sleep(backoff_seconds)
                task.attempt += 1
                with self._metrics_lock:
                    self._retried += 1
                if not self.submit(task):
                    LOGGER.error("Queue full while retrying sid=%s from=%s", task.inbound_sid or "-", task.from_number)
            else:
                with self._metrics_lock:
                    self._failed += 1
                LOGGER.exception(
                    "Queued turn permanently failed sid=%s from=%s attempts=%d",
                    task.inbound_sid or "-",
                    task.from_number,
                    task.attempt + 1,
                )
