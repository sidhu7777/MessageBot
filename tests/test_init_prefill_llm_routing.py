import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


class _StubLLM:
    def __init__(self, prefill_json: str):
        self._prefill_json = prefill_json

    def generate(self, system: str, user: str) -> str:
        s = (system or "").lower()
        if "booking prefill fields from first user message" in s:
            return self._prefill_json
        return "{}"


class _BookingRepo:
    def default_admin_id(self):
        return 1

    def get_doctor_display_name(self, doctor_id, admin_id=None):
        return "Sanjay Vinayak"

    def list_active_appointments_by_phone_number(self, phone_number, admin_id=None, doctor_id=None, limit: int = 10):
        return []

    def find_patient_name_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None):
        return "Vineeth"


@dataclass
class _Clinic:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


class _SchedulingRepo:
    def default_doctor_id(self, admin_id=None):
        return 1

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [_Clinic(1, "City Care Clinic", "Delhi", 3)][:limit]

    def doctor_accept_days(self, doctor_id: int, admin_id=None):
        return 2

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 3):
        return ["2026-02-24", "2026-02-25"][:limit]

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 60):
        return ["17:00", "17:10", "17:20"][:limit]


def _new_fsm(prefill_json: str) -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=_StubLLM(prefill_json),
        enable_llm_polish=True,
        booking_repository=_BookingRepo(),
        scheduling_repository=_SchedulingRepo(),
        mixed_response_language="auto",
    )
    fsm.chat_phone_number = "whatsapp:+919392569600"
    return fsm


def test_init_prefill_full_data_goes_direct_to_confirm() -> None:
    prefill = (
        '{"patient_name":"Vineeth","appointment_date":"2026-02-24",'
        '"appointment_time":"17:00","clinic_name":"City Care Clinic","booking_for":"self"}'
    )
    fsm = _new_fsm(prefill)
    reply = fsm.handle("Hi, my name is Vineeth and I want to book an appointment today at 5 PM for me.")

    # INIT is rule-based now; prefill must not short-circuit to CONFIRM.
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "who is this appointment for" in reply.lower()


def test_init_prefill_partial_data_asks_only_missing_clinic() -> None:
    prefill = (
        '{"patient_name":"Vineeth","appointment_date":"2026-02-24",'
        '"appointment_time":"17:00","clinic_name":"","booking_for":"self"}'
    )
    fsm = _new_fsm(prefill)
    reply = fsm.handle("I am Vineeth. Please book for today at 5 PM.")

    # INIT is rule-based now; prefill must not short-circuit to ASK_CLINIC.
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "who is this appointment for" in reply.lower()
