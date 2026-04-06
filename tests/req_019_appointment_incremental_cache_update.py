"""
REQ-019: Appointment Incremental Redis Update

Run:
  python tests/req_019_appointment_incremental_cache_update.py
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.scheduling_repository import CacheInvalidationEvent, SchedulingRepository

PASS = 0
FAIL = 0


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str, ex=None):
        self.data[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.data.pop(str(key), None)
        return len(keys)

    def scan_iter(self, match=None, count=10):
        for key in list(self.data.keys()):
            if not match or str(match).replace("*", "") in key:
                yield key


class DummyRepo(SchedulingRepository):
    def __init__(self, redis_client):
        super().__init__(config=object(), redis_client=redis_client, cache_ttl_seconds=3600, cache_key_prefix="z")


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


def test_incremental_update() -> None:
    print("\n[TEST] book/remove + cancel/add on cached times")
    redis = FakeRedis()
    repo = DummyRepo(redis)
    today = date.today().isoformat()
    key = repo._availability_cache_key(doctor_id=50, admin_id=9)
    snapshot = {
        "doctor_id": 50,
        "admin_id": 9,
        "accept_days": 1,
        "generated_on": today,
        "clinics": [{"clinic_id": 2, "clinic_name": "A", "location": "X", "today_slots": 0}],
        "dates_by_clinic": {"2": [today]},
        "times_by_clinic_date": {f"2|{today}": ["10:00", "10:30", "11:00"]},
    }
    redis.data[key] = json.dumps(snapshot)

    # Simulate new BOOKED appointment at 10:30 => remove from free list.
    book_event = CacheInvalidationEvent(
        queue_id=1,
        entity_type="APPOINTMENT",
        doctor_id=50,
        clinic_id=2,
        admin_id=9,
        slot_date=today,
        slot_time="10:30",
        new_status="BOOKED",
    )
    repo.process_cache_invalidation_event(book_event)
    payload = json.loads(redis.data[key])
    times = payload["times_by_clinic_date"][f"2|{today}"]
    check("Booked slot removed from cache", "10:30" not in times, str(times))

    # Simulate cancel of same appointment => old occupied slot becomes free again.
    cancel_event = CacheInvalidationEvent(
        queue_id=2,
        entity_type="APPOINTMENT",
        old_doctor_id=50,
        old_clinic_id=2,
        old_admin_id=9,
        old_slot_date=today,
        old_slot_time="10:30",
        old_status="BOOKED",
    )
    repo.process_cache_invalidation_event(cancel_event)
    payload = json.loads(redis.data[key])
    times = payload["times_by_clinic_date"][f"2|{today}"]
    check("Cancelled slot added back", "10:30" in times, str(times))
    check("Times remain sorted", times == sorted(times), str(times))


if __name__ == "__main__":
    print("=" * 72)
    print("REQ-019: Appointment Incremental Redis Update")
    print("=" * 72)
    test_incremental_update()
    print("\n" + "=" * 72)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 72)
    sys.exit(0 if FAIL == 0 else 1)
