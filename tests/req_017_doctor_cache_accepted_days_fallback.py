"""
REQ-017: Doctor Availability Redis Cache (accepted-days window + DB fallback)

Run:
  python tests/req_017_doctor_cache_accepted_days_fallback.py
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.scheduling_repository import ClinicOption, SchedulingRepository

PASS = 0
FAIL = 0


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.set_calls = 0

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str, ex=None):
        self.data[key] = value
        self.set_calls += 1
        return True


class DummySchedulingRepository(SchedulingRepository):
    def __init__(self, redis_client):
        super().__init__(config=object(), redis_client=redis_client, cache_ttl_seconds=300, cache_key_prefix="t")
        self.accept_calls = 0
        self.clinic_calls = 0
        self.time_calls = 0

    def doctor_accept_days(self, doctor_id: int, admin_id=None) -> int:
        self.accept_calls += 1
        return 1  # today + tomorrow only

    def _db_list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        self.clinic_calls += 1
        return [
            ClinicOption(1, "City Care", "MG Road", 0),
            ClinicOption(2, "Sunrise", "KPHB", 0),
        ]

    def _db_list_available_times_for_date(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None):
        self.time_calls += 1
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        if clinic_id == 1 and slot_date == today:
            return ["10:00", "10:30"]
        if clinic_id == 2 and slot_date == tomorrow:
            return ["11:00"]
        return []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


def test_cache_and_fallback() -> None:
    print("\n[TEST] accepted-days snapshot cache + fallback")
    redis = FakeRedis()
    repo = DummySchedulingRepository(redis)

    dates_first = repo.list_available_dates(doctor_id=10, clinic_id=1, admin_id=7, limit=3)
    check("First call builds cache via DB", repo.clinic_calls == 1 and repo.time_calls == 4, f"clinic={repo.clinic_calls} time={repo.time_calls}")
    check("Dates are only from accepted window", len(dates_first) == 1 and dates_first[0] == date.today().isoformat(), str(dates_first))
    check("Redis snapshot written", redis.set_calls == 1, f"set_calls={redis.set_calls}")

    before_time_calls = repo.time_calls
    dates_second = repo.list_available_dates(doctor_id=10, clinic_id=1, admin_id=7, limit=3)
    after_dates_second_calls = repo.time_calls
    times_cached = repo.list_available_times(doctor_id=10, clinic_id=1, slot_date=date.today().isoformat(), admin_id=7, limit=5)
    check("Second dates call served from cache", after_dates_second_calls == before_time_calls, f"time_calls={after_dates_second_calls}")
    check("Today's times served from live DB", repo.time_calls == after_dates_second_calls + 1, f"time_calls={repo.time_calls}")
    check("Today's times remain correct", times_cached == ["10:00", "10:30"], str(times_cached))
    check("Dates stable on cache hit", dates_second == dates_first, f"{dates_second} vs {dates_first}")

    before_future_calls = repo.time_calls
    times_future_cached = repo.list_available_times(
        doctor_id=10,
        clinic_id=2,
        slot_date=(date.today() + timedelta(days=1)).isoformat(),
        admin_id=7,
        limit=5,
    )
    check("Future times still served from cache", repo.time_calls == before_future_calls, f"time_calls={repo.time_calls}")
    check("Future cached times remain correct", times_future_cached == ["11:00"], str(times_future_cached))

    # Corrupt payload for one key -> should fallback to DB for that lookup only.
    cache_key = repo._availability_cache_key(doctor_id=10, admin_id=7)
    payload = json.loads(redis.data[cache_key])
    payload["times_by_clinic_date"].pop(f"1|{date.today().isoformat()}", None)
    redis.data[cache_key] = json.dumps(payload)
    before_fallback_calls = repo.time_calls
    times_fallback = repo.list_available_times(doctor_id=10, clinic_id=1, slot_date=date.today().isoformat(), admin_id=7, limit=5)
    check("Today's lookup remains live DB even after cache edit", repo.time_calls == before_fallback_calls + 1, f"time_calls={repo.time_calls}")
    check("Today's live DB still returns correct times", times_fallback == ["10:00", "10:30"], str(times_fallback))


if __name__ == "__main__":
    print("=" * 72)
    print("REQ-017: Doctor Availability Redis Cache")
    print("=" * 72)

    test_cache_and_fallback()

    print("\n" + "=" * 72)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 72)
    sys.exit(0 if FAIL == 0 else 1)
