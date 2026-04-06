import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient


class _SnapshotRepo:
    def __init__(self) -> None:
        self.snapshot_calls = 0
        self.list_clinics_calls = 0
        self.list_times_calls = 0
        self.list_dates_calls = 0

    def get_availability_snapshot(self, doctor_id: int, admin_id=None):
        self.snapshot_calls += 1
        return {
            "doctor_id": int(doctor_id),
            "admin_id": int(admin_id) if admin_id is not None else None,
            "accept_days": 1,
            "generated_on": "2026-03-06",
            "clinics": [
                {"clinic_id": 2, "clinic_name": "Health Plus Clinic", "location": "Noida", "today_slots": 0},
                {"clinic_id": 1, "clinic_name": "City Care Clinic", "location": "Delhi", "today_slots": 0},
            ],
            "dates_by_clinic": {
                "2": ["2026-03-06"],
                "1": [],
            },
            "times_by_clinic_date": {
                "2|2026-03-06": ["09:00", "09:30", "10:00", "16:30"],
            },
        }

    # These are intentionally here to ensure the new path does not use them.
    def list_clinics_for_doctor(self, *args, **kwargs):
        self.list_clinics_calls += 1
        raise AssertionError("Should not call list_clinics_for_doctor in snapshot-primary path")

    def list_available_times(self, *args, **kwargs):
        self.list_times_calls += 1
        raise AssertionError("Should not call list_available_times in snapshot-primary path")

    def list_available_dates(self, *args, **kwargs):
        self.list_dates_calls += 1
        raise AssertionError("Should not call list_available_dates in snapshot-primary path")


def test_availability_reply_uses_snapshot_primary_path():
    repo = _SnapshotRepo()
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b"),
        scheduling_repository=repo,
    )
    fsm.doctor_id = 1
    fsm.admin_id = 1

    reply = fsm._availability_reply("2026-03-06")
    assert "Doctor availability on 2026-03-06:" in reply
    assert "- Health Plus Clinic: 4 slots (09:00 AM - 04:30 PM)" in reply
    assert "1. Book appointment" in reply
    assert repo.snapshot_calls == 1
    assert repo.list_clinics_calls == 0
    assert repo.list_times_calls == 0
    assert repo.list_dates_calls == 0
