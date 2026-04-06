"""
REQ-018: Doctor Cache Invalidation Logic

Run:
  python tests/req_018_doctor_cache_invalidation_logic.py
"""

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
        self.keys = set()
        self.deleted = []

    def scan_iter(self, match=None, count=10):
        if not match:
            for key in list(self.keys):
                yield key
            return
        # Very small matcher for suffix-based pattern used in code.
        token = str(match).replace("*", "")
        for key in list(self.keys):
            if token in key:
                yield key

    def delete(self, *keys):
        for key in keys:
            self.deleted.append(str(key))
            self.keys.discard(str(key))
        return len(keys)


class DummyRepo(SchedulingRepository):
    def __init__(self, redis_client):
        super().__init__(config=object(), redis_client=redis_client, cache_ttl_seconds=3600, cache_key_prefix="k")

    def _doctor_ids_for_clinic(self, clinic_id: int, admin_id):
        if int(clinic_id) == 77:
            return [11, 12]
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


def test_doctor_and_clinic_invalidation() -> None:
    print("\n[TEST] trigger-event invalidation routes")
    redis = FakeRedis()
    repo = DummyRepo(redis)
    today_key = date.today().isoformat().replace("-", "")

    # Seed keys.
    redis.keys.update(
        {
            f"k:avail:5:11:{today_key}",
            f"k:avail:na:11:{today_key}",
            f"k:avail:5:12:{today_key}",
            f"k:avail:na:12:{today_key}",
        }
    )

    doctor_event = CacheInvalidationEvent(
        queue_id=1,
        entity_type="DOCTOR",
        doctor_id=11,
        clinic_id=None,
        admin_id=5,
    )
    repo.process_cache_invalidation_event(doctor_event)
    check(
        "Doctor event removed doctor 11 keys",
        (f"k:avail:5:11:{today_key}" in redis.deleted) or (f"k:avail:na:11:{today_key}" in redis.deleted),
    )

    clinic_event = CacheInvalidationEvent(
        queue_id=2,
        entity_type="CLINIC",
        doctor_id=None,
        clinic_id=77,
        admin_id=5,
    )
    repo.process_cache_invalidation_event(clinic_event)
    removed_12 = (f"k:avail:5:12:{today_key}" in redis.deleted) or (f"k:avail:na:12:{today_key}" in redis.deleted)
    check("Clinic event fan-outs to mapped doctors", removed_12)


if __name__ == "__main__":
    print("=" * 72)
    print("REQ-018: Doctor Cache Invalidation Logic")
    print("=" * 72)
    test_doctor_and_clinic_invalidation()
    print("\n" + "=" * 72)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 72)
    sys.exit(0 if FAIL == 0 else 1)
