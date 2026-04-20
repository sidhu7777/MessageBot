from __future__ import annotations

import threading
import time
from typing import Optional


class EvolutionAutoResponsePolicy:
    def __init__(
        self,
        *,
        redis_client: Optional[object] = None,
        session_window_seconds: int = 6 * 60 * 60,
        key_prefix: str = "msgbot",
    ) -> None:
        self._redis = redis_client
        self._session_window_seconds = max(60, int(session_window_seconds))
        self._key_prefix = (key_prefix or "msgbot").strip() or "msgbot"
        self._local_lock = threading.Lock()
        self._local_counts: dict[str, tuple[float, int]] = {}

    def register_inbound(self, *, doctor_id: int, patient_identity: str) -> int:
        key = self._key(int(doctor_id), patient_identity)
        if self._redis is not None:
            try:
                count = int(self._redis.incr(key))
                if count == 1:
                    self._redis.expire(key, self._session_window_seconds)
                return count
            except Exception:
                pass
        return self._register_inbound_local(key)

    def _key(self, doctor_id: int, patient_identity: str) -> str:
        normalized = self._normalize_patient_identity(patient_identity)
        return f"{self._key_prefix}:evolution:auto:{doctor_id}:{normalized}"

    @staticmethod
    def _normalize_patient_identity(value: str) -> str:
        raw = str(value or "").strip().lower()
        if "@" in raw:
            raw = raw.split("@", 1)[0].strip()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :].strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        return digits or raw or "unknown"

    def _register_inbound_local(self, key: str) -> int:
        now = time.time()
        with self._local_lock:
            started_at, count = self._local_counts.get(key, (0.0, 0))
            if (now - float(started_at)) >= self._session_window_seconds:
                started_at, count = now, 0
            count += 1
            self._local_counts[key] = (started_at or now, count)
            self._prune_local(now)
            return count

    def _prune_local(self, now_ts: float) -> None:
        expired = [
            key
            for key, (started_at, _count) in self._local_counts.items()
            if (now_ts - float(started_at)) >= self._session_window_seconds
        ]
        for key in expired:
            self._local_counts.pop(key, None)


__all__ = ["EvolutionAutoResponsePolicy"]
