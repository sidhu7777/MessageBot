import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient


class _FlowRepo:
    def __init__(self) -> None:
        self.snapshot_calls = 0
        self.accept_days_calls = 0

    def doctor_accept_days(self, doctor_id: int, admin_id=None) -> int:
        self.accept_days_calls += 1
        return 1

    def get_availability_snapshot(self, doctor_id: int, admin_id=None) -> dict:
        self.snapshot_calls += 1
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return {
            "doctor_id": int(doctor_id),
            "admin_id": int(admin_id) if admin_id is not None else None,
            "accept_days": 1,
            "generated_on": today,
            "clinics": [
                {"clinic_id": 2, "clinic_name": "Health Plus Clinic", "location": "Noida", "today_slots": 0},
            ],
            "dates_by_clinic": {"2": [tomorrow]},
            "times_by_clinic_date": {"2|" + tomorrow: ["09:00", "09:30", "10:00", "16:30"]},
        }


def _new_fsm(repo: _FlowRepo) -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", provider="ollama"),
        enable_llm_polish=False,
        mixed_response_language="auto",
        scheduling_repository=repo,
    )
    fsm.doctor_id = 1
    fsm.admin_id = 1
    return fsm


def test_availability_flow_and_go_back_path_with_snapshot():
    repo = _FlowRepo()
    fsm = _new_fsm(repo)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # INIT -> ASK_AVAILABILITY_DATE
    r1 = fsm.handle("2")
    assert fsm.state == "ASK_AVAILABILITY_DATE"
    assert "Please choose a date to check availability:" in r1
    assert 'Press "0" to go back.' in r1

    # ASK_AVAILABILITY_DATE -> ASK_AVAILABILITY_DETAILS
    r2 = fsm.handle("2")
    assert fsm.state == "ASK_AVAILABILITY_DETAILS"
    assert f"Doctor availability on {tomorrow}:" in r2
    assert "- Health Plus Clinic: 4 slots (09:00 AM - 04:30 PM)" in r2
    assert "1. Book appointment" in r2
    assert "0. Go back" in r2
    assert repo.snapshot_calls >= 1

    # 0 from details -> date menu
    r3 = fsm.handle("0")
    assert fsm.state == "ASK_AVAILABILITY_DATE"
    assert "Please choose a date to check availability:" in r3

    # 0 from date menu -> INIT
    r4 = fsm.handle("0")
    assert fsm.state == "INIT"
    assert "How can I help you today?" in r4
