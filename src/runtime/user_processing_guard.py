import os
import threading
import time
from typing import Optional


class UserProcessingGuard:
    def __init__(
        self,
        *,
        redis_client: Optional[object] = None,
        lock_ttl_seconds: int = 45,
        busy_ttl_seconds: int = 8,
        key_prefix: str = "msgbot",
        redis_op_timeout_ms: int = 40,
        fail_open_cooldown_seconds: float = 2.0,
    ) -> None:
        self._redis = redis_client
        self._lock_ttl_seconds = max(5, int(lock_ttl_seconds))
        self._busy_ttl_seconds = max(2, int(busy_ttl_seconds))
        self._key_prefix = (key_prefix or "msgbot").strip() or "msgbot"
        self._redis_op_timeout_seconds = max(0.005, float(redis_op_timeout_ms) / 1000.0)
        self._fail_open_cooldown_seconds = max(0.2, float(fail_open_cooldown_seconds))
        self._redis_disabled_until = 0.0
        self._guard_lock = threading.Lock()
        # In-process fallback guard used when Redis is not available.
        # Maps user_id -> acquire timestamp (monotonic seconds).
        self._local_held: dict[str, float] = {}
        self._local_lock = threading.Lock()

    def _lock_key(self, user_id: str) -> str:
        return f"{self._key_prefix}:proc:{(user_id or '').strip()}"

    def _busy_key(self, user_id: str) -> str:
        return f"{self._key_prefix}:busy:{(user_id or '').strip()}"

    def acquire(self, user_id: str) -> bool:
        uid = (user_id or "").strip()
        if not uid:
            return True
        if not self._redis:
            return self._local_acquire(uid)
        lock_key = self._lock_key(uid)
        return bool(
            self._call_redis(
                op=lambda: self._redis.set(lock_key, "1", nx=True, ex=self._lock_ttl_seconds),
                default=self._local_acquire(uid),
            )
        )

    def release(self, user_id: str) -> None:
        uid = (user_id or "").strip()
        if not uid:
            return
        self._local_release(uid)
        if not self._redis:
            return
        lock_key = self._lock_key(uid)
        self._call_redis(op=lambda: self._redis.delete(lock_key), default=None)

    def _local_acquire(self, uid: str) -> bool:
        """Try to acquire the in-process guard. Held entries expire after lock_ttl_seconds."""
        now = time.monotonic()
        with self._local_lock:
            held_at = self._local_held.get(uid)
            if held_at is not None and (now - held_at) < self._lock_ttl_seconds:
                return False
            self._local_held[uid] = now
            return True

    def _local_release(self, uid: str) -> None:
        with self._local_lock:
            self._local_held.pop(uid, None)

    def allow_busy_hint(self, user_id: str) -> bool:
        uid = (user_id or "").strip()
        if not uid:
            return False
        if not self._redis:
            return False
        busy_key = self._busy_key(uid)
        return bool(
            self._call_redis(
                op=lambda: self._redis.set(busy_key, "1", nx=True, ex=self._busy_ttl_seconds),
                default=False,
            )
        )

    def _redis_disabled(self) -> bool:
        with self._guard_lock:
            return time.monotonic() < self._redis_disabled_until

    def _mark_redis_failure(self) -> None:
        with self._guard_lock:
            self._redis_disabled_until = time.monotonic() + self._fail_open_cooldown_seconds

    def _call_redis(self, *, op, default):
        if not self._redis:
            return default
        if self._redis_disabled():
            return default
        try:
            return op()
        except Exception:
            self._mark_redis_failure()
            return default


def build_redis_client_from_env() -> Optional[object]:
    try:
        import redis
    except Exception:
        return None

    host = os.getenv("REDIS_HOST", "localhost").strip() or "localhost"
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD", "") or None
    socket_timeout = max(0.01, float(os.getenv("REDIS_SOCKET_TIMEOUT_MS", "80")) / 1000.0)
    connect_timeout = max(0.01, float(os.getenv("REDIS_CONNECT_TIMEOUT_MS", "80")) / 1000.0)

    try:
        client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
            retry_on_timeout=False,
        )
        client.ping()
        return client
    except Exception:
        return None
