import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from src.runtime.turn_queue import TurnTask


@dataclass
class BufferPushResult:
    pending_count: int
    collapsed: bool
    dropped_oldest: bool


class UserTurnBuffer:
    def __init__(self, *, max_per_user: int = 5, collapse_window_seconds: float = 6.0) -> None:
        self._max_per_user = max(1, int(max_per_user))
        self._collapse_window_seconds = max(0.0, float(collapse_window_seconds))
        self._lock = threading.Lock()
        self._queues: Dict[str, Deque[TurnTask]] = {}
        # Tracks the last dispatched (body, timestamp) per user so that buffered
        # messages arriving while that turn is processing can collapse against it.
        self._last_dispatch: Dict[str, tuple] = {}

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join((text or "").strip().lower().split())

    @classmethod
    def _init_intent_priority(cls, text: str) -> int:
        t = cls._norm(text)
        if not t:
            return 0
        booking_tokens = (
            "book",
            "booking",
            "appointment",
            "appoint",
            "chahiye",
            "chaiye",
            "consult",
            "doctor se milna",
            "doctor se milna hai",
            "अपॉइंटमेंट",
            "बुक",
            "बुकिंग",
            "मिलना है",
            "चाहिए",
        )
        availability_tokens = (
            "availability",
            "available",
            "slot",
            "free",
            "khali",
            "time milega",
            "available hai",
            "availability chahiye",
            "slot chahiye",
            "समय",
            "खाली",
            "उपलब्ध",
            "स्लॉट",
        )
        greeting_tokens = ("hi", "hello", "hey", "hii", "namaste", "namaskar")
        if any(tok in t for tok in booking_tokens):
            return 3
        if any(tok in t for tok in availability_tokens):
            return 2
        if t in greeting_tokens or len(t.split()) <= 2:
            return 0
        return 1

    @classmethod
    def _bodies_are_flood(cls, body_a: str, body_b: str) -> bool:
        """Return True when both messages are effectively the same low-intent noise."""
        na = cls._norm(body_a)
        nb = cls._norm(body_b)
        if na == nb:
            return True
        # Both are low-priority (greeting / 1-word) — treat as same flood.
        if cls._init_intent_priority(na) == 0 and cls._init_intent_priority(nb) == 0:
            return True
        return False

    def record_dispatch(self, user_id: str, body: str) -> None:
        """Call this immediately before submitting a task directly to a worker.
        Subsequent buffered messages with the same body within the collapse window
        will be silently collapsed instead of queued."""
        uid = (user_id or "").strip()
        if not uid:
            return
        with self._lock:
            self._last_dispatch[uid] = (body, time.time())

    def push(self, task: TurnTask) -> BufferPushResult:
        user_id = (task.from_number or "").strip()
        if not user_id:
            return BufferPushResult(pending_count=0, collapsed=False, dropped_oldest=False)
        now = float(task.enqueue_ts or time.time())
        with self._lock:
            # ── flood-collapse against already-dispatched turn ──────────────────
            dispatch_entry = self._last_dispatch.get(user_id)
            if dispatch_entry is not None:
                dispatched_body, dispatched_ts = dispatch_entry
                within_window = (now - dispatched_ts) <= self._collapse_window_seconds
                if within_window and self._bodies_are_flood(dispatched_body, task.body):
                    # Drop silently — the dispatched turn will handle the user.
                    q = self._queues.get(user_id)
                    return BufferPushResult(
                        pending_count=len(q) if q else 0,
                        collapsed=True,
                        dropped_oldest=False,
                    )

            q = self._queues.get(user_id)
            if q is None:
                q = deque()
                self._queues[user_id] = q

            collapsed = False
            dropped_oldest = False
            if q:
                last = q[-1]
                same_state = (last.pre_state or "").strip() == (task.pre_state or "").strip()
                within_window = (now - float(last.enqueue_ts or now)) <= self._collapse_window_seconds
                if within_window and same_state and (task.pre_state or "").strip() == "INIT":
                    if self._bodies_are_flood(last.body, task.body):
                        # Keep whichever has higher booking intent.
                        old_priority = self._init_intent_priority(last.body)
                        new_priority = self._init_intent_priority(task.body)
                        if new_priority >= old_priority:
                            q[-1] = task
                        collapsed = True
                        return BufferPushResult(pending_count=len(q), collapsed=collapsed, dropped_oldest=dropped_oldest)

            q.append(task)
            if len(q) > self._max_per_user:
                q.popleft()
                dropped_oldest = True
            return BufferPushResult(pending_count=len(q), collapsed=collapsed, dropped_oldest=dropped_oldest)

    def push_front(self, task: TurnTask) -> int:
        user_id = (task.from_number or "").strip()
        if not user_id:
            return 0
        with self._lock:
            q = self._queues.get(user_id)
            if q is None:
                q = deque()
                self._queues[user_id] = q
            q.appendleft(task)
            while len(q) > self._max_per_user:
                q.pop()
            return len(q)

    def pop_next(self, user_id: str) -> Optional[TurnTask]:
        uid = (user_id or "").strip()
        if not uid:
            return None
        with self._lock:
            q = self._queues.get(uid)
            if not q:
                return None
            task = q.popleft()
            if not q:
                self._queues.pop(uid, None)
            return task

    def pending_count(self, user_id: str) -> int:
        uid = (user_id or "").strip()
        if not uid:
            return 0
        with self._lock:
            q = self._queues.get(uid)
            return len(q) if q else 0
