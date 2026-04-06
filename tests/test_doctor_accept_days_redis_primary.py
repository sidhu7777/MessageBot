import json
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.connection import MySQLConfig
from src.repositories.scheduling_repository import SchedulingRepository


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.data[key] = value
        return True


class _FakeCursor:
    def __init__(self, *, accept_days: int, db_sleep_seconds: float) -> None:
        self._accept_days = int(accept_days)
        self._db_sleep_seconds = float(db_sleep_seconds)
        self._rows: list[dict] = []

    def execute(self, query: str, params=None) -> None:
        q = str(query or "").lower()
        # Simulate remote DB latency for doctor_accept_days path.
        if "information_schema.columns" in q and "table_name = 'doctors'" in q:
            time.sleep(self._db_sleep_seconds)
            self._rows = [{"COLUMN_NAME": "accept_days"}]
            return
        if "select accept_days as accept_days" in q and "from doctors" in q:
            time.sleep(self._db_sleep_seconds)
            self._rows = [{"accept_days": self._accept_days}]
            return
        self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        return


class _FakeConnection:
    def __init__(self, *, accept_days: int, db_sleep_seconds: float) -> None:
        self._accept_days = int(accept_days)
        self._db_sleep_seconds = float(db_sleep_seconds)

    def cursor(self, dictionary: bool = False):
        return _FakeCursor(accept_days=self._accept_days, db_sleep_seconds=self._db_sleep_seconds)

    def close(self) -> None:
        return


class _ProbeRepo(SchedulingRepository):
    def __init__(self, *, redis_client, accept_days: int, db_sleep_seconds: float = 0.06) -> None:
        super().__init__(
            config=MySQLConfig(user="u", password="p", host="h", port=3306, database="d"),
            redis_client=redis_client,
            cache_ttl_seconds=3600,
            cache_key_prefix="msgbot",
        )
        self._probe_accept_days = int(accept_days)
        self._probe_db_sleep_seconds = float(db_sleep_seconds)
        self.connect_calls = 0

    def _connect(self):
        self.connect_calls += 1
        return _FakeConnection(
            accept_days=self._probe_accept_days,
            db_sleep_seconds=self._probe_db_sleep_seconds,
        )


def test_doctor_accept_days_uses_redis_before_db():
    redis = _FakeRedis()
    repo = _ProbeRepo(redis_client=redis, accept_days=9, db_sleep_seconds=0.06)

    key = repo._availability_cache_key(doctor_id=1, admin_id=1)
    redis.data[key] = json.dumps({"accept_days": 1, "generated_on": "2026-03-05"})

    t0 = time.perf_counter()
    got = repo.doctor_accept_days(doctor_id=1, admin_id=1)
    elapsed = time.perf_counter() - t0

    assert got == 1
    assert repo.connect_calls == 0, "Redis hit path should not call DB"
    assert elapsed < 0.02, f"Redis path should be fast; got {elapsed:.4f}s"


def test_doctor_accept_days_falls_back_to_db_on_cache_miss():
    redis = _FakeRedis()
    repo = _ProbeRepo(redis_client=redis, accept_days=7, db_sleep_seconds=0.05)

    t0 = time.perf_counter()
    got = repo.doctor_accept_days(doctor_id=1, admin_id=1)
    elapsed = time.perf_counter() - t0

    assert got == 7
    assert repo.connect_calls >= 1, "Cache miss should query DB"
    assert elapsed >= 0.05, f"DB fallback path should reflect DB latency; got {elapsed:.4f}s"
