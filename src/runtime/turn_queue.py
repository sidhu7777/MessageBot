import logging
import queue
import threading
import time
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple


LOGGER = logging.getLogger(__name__)


@dataclass
class TurnTask:
    from_number: str
    body: str
    inbound_sid: str
    pre_state: str
    attempt: int = 0
    enqueue_ts: float = field(default_factory=time.time)
    send_started: threading.Event = field(default_factory=threading.Event)
    timeout_notified: threading.Event = field(default_factory=threading.Event)


class TurnQueueProcessor:
    def __init__(
        self,
        *,
        worker_count: int,
        max_queue_size: int,
        process_fn: Callable[[str, str], Tuple[Any, ...]],
        send_fn: Callable[..., None],
        retry_attempts: int = 1,
        processing_timeout_seconds: float = 0.0,
        timeout_fn: Optional[Callable[[TurnTask, Exception], None]] = None,
        on_success: Optional[Callable[[TurnTask], None]] = None,
        on_failure: Optional[Callable[[TurnTask, Exception, bool, float], None]] = None,
    ) -> None:
        self.worker_count = worker_count
        self.retry_attempts = max(0, retry_attempts)
        self._processing_timeout_seconds = max(0.0, float(processing_timeout_seconds))
        self._timeout_grace_seconds = 0.75
        self._process_fn = process_fn
        self._send_fn = send_fn
        try:
            self._send_fn_accepts_fsm = len(inspect.signature(send_fn).parameters) >= 5
        except Exception:
            self._send_fn_accepts_fsm = False
        self._timeout_fn = timeout_fn
        self._on_success = on_success
        self._on_failure = on_failure
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
                "processing_timeout_seconds": self._processing_timeout_seconds,
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
        done_event = threading.Event()
        if self._timeout_fn and self._processing_timeout_seconds > 0:
            timeout_seconds = self._processing_timeout_seconds

            def _timeout_watchdog() -> None:
                if done_event.wait(timeout=timeout_seconds):
                    return
                if task.send_started.wait(timeout=self._timeout_grace_seconds):
                    return
                if task.timeout_notified.is_set():
                    return
                task.timeout_notified.set()
                try:
                    self._timeout_fn(
                        task,
                        TimeoutError(f"Turn processing timeout after {timeout_seconds:.1f}s"),
                    )
                except Exception:
                    LOGGER.exception("Failed to send timeout-safe message sid=%s", task.inbound_sid or "-")

            threading.Thread(
                target=_timeout_watchdog,
                name=f"turn-timeout-{task.inbound_sid or 'nosid'}",
                daemon=True,
            ).start()
        try:
            result = self._process_fn(task.from_number, task.body)
            fsm = None
            if isinstance(result, tuple) and len(result) == 3:
                reply, post_state, fsm = result
            else:
                reply, post_state = result
            task.send_started.set()
            if self._send_fn_accepts_fsm:
                self._send_fn(task.from_number, reply, post_state, task.inbound_sid, fsm)
            else:
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
            if self._on_success:
                self._on_success(task)
        except Exception as exc:
            is_timeout = isinstance(exc, TimeoutError)
            # Timeouts are NOT retried — Ollama is still busy, retrying causes another timeout
            can_retry = (
                not is_timeout
                and task.attempt < self.retry_attempts
                and not self._stop.is_set()
            )

            if can_retry:
                backoff_seconds = min(4.0, 0.8 * (2 ** task.attempt))
                if self._on_failure:
                    self._on_failure(task, exc, True, backoff_seconds)
                LOGGER.warning(
                    "Queued turn failed; retrying sid=%s from=%s attempt=%d/%d after %.1fs error=%s",
                    task.inbound_sid or "-",
                    task.from_number,
                    task.attempt + 1,
                    self.retry_attempts + 1,
                    backoff_seconds,
                    exc,
                )
                task.attempt += 1
                with self._metrics_lock:
                    self._retried += 1
                retry_timer = threading.Timer(backoff_seconds, self._submit_retry_task, args=(task,))
                retry_timer.daemon = True
                retry_timer.start()
            else:
                # Send "busy" message only once — on final failure
                if is_timeout and self._timeout_fn and not task.timeout_notified.is_set():
                    task.timeout_notified.set()
                    try:
                        self._timeout_fn(task, exc)
                    except Exception:
                        LOGGER.exception("Failed to send timeout-safe message sid=%s", task.inbound_sid or "-")
                if self._on_failure:
                    self._on_failure(task, exc, False, 0.0)
                with self._metrics_lock:
                    self._failed += 1
                LOGGER.exception(
                    "Queued turn permanently failed sid=%s from=%s attempts=%d",
                    task.inbound_sid or "-",
                    task.from_number,
                    task.attempt + 1,
                )
        finally:
            done_event.set()

    def _submit_retry_task(self, task: TurnTask) -> None:
        if self._stop.is_set():
            return
        if self.submit(task):
            return
        err = RuntimeError("Queue full while retrying")
        LOGGER.error("%s sid=%s from=%s", err, task.inbound_sid or "-", task.from_number)
        if self._on_failure:
            self._on_failure(task, err, False, 0.0)
        with self._metrics_lock:
            self._failed += 1
