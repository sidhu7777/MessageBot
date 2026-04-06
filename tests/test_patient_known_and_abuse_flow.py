from dataclasses import dataclass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


class _StubLLM:
    def generate(self, system: str, user: str) -> str:
        # Used only in fallback abuse detection test.
        if "abusive" in (system or "").lower():
            return '{"label":"ABUSE","confidence":0.95}'
        return "UNKNOWN"


@dataclass
class _Clinic:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


class _BaseBookingRepo:
    def default_admin_id(self):
        return 1

    def get_doctor_display_name(self, doctor_id, admin_id=None):
        return "Sanjay Vinayak"

    def list_active_appointments_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None, limit: int = 10):
        return []


class _KnownBookingRepo(_BaseBookingRepo):
    def find_patient_name_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None):
        return "Vineeth Raja"


class _NewBookingRepo(_BaseBookingRepo):
    def find_patient_name_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None):
        return None


class _SchedulingRepo:
    def default_doctor_id(self, admin_id=None):
        return 1

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [
            _Clinic(1, "City Care Clinic", "Delhi", 3),
            _Clinic(2, "Sunrise Health Center", "Noida", 2),
        ][:limit]

    def doctor_accept_days(self, doctor_id: int, admin_id=None):
        return 2

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 3):
        return ["2026-02-24", "2026-02-25"][:limit]

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 3):
        return ["10:00", "10:10", "10:20"][:limit]


def _new_fsm(booking_repo, *, enable_llm_polish: bool = False) -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=_StubLLM(),
        enable_llm_polish=enable_llm_polish,
        booking_repository=booking_repo,
        scheduling_repository=_SchedulingRepo(),
        mixed_response_language="auto",
    )
    fsm.chat_phone_number = "whatsapp:+919392569600"
    return fsm


def test_case1_new_patient_flow_starts_with_name_prompt() -> None:
    fsm = _new_fsm(_NewBookingRepo())

    r1 = fsm.handle("Hello")
    assert "welcome to dr." in r1.lower()

    r2 = fsm.handle("I need to book an appointment")
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "who is this appointment for?" in r2.lower()

    r3 = fsm.handle("1")
    assert fsm.state == "ASK_NAME"
    assert "please share the patient full name" in r3.lower()


def test_case2_known_patient_self_skips_name_and_phone_to_clinic() -> None:
    fsm = _new_fsm(_KnownBookingRepo())

    r1 = fsm.handle("Hello")
    assert "vineeth raja" in r1.lower()

    fsm.handle("I need to book an appointment")
    r3 = fsm.handle("1")

    assert fsm.state == "ASK_CLINIC"
    assert "please choose clinic" in r3.lower()
    assert "please share the patient full name" not in r3.lower()
    assert "is the contact number same as this whatsapp number" not in r3.lower()


def test_case3_abusive_language_blocked_at_start_and_middle() -> None:
    # Start-turn abuse via rule list.
    fsm_start = _new_fsm(_NewBookingRepo())
    r1 = fsm_start.handle("fuck you")
    assert "respectful language" in r1.lower()
    assert fsm_start.state == "INIT"

    # Middle-turn abuse via rule list while waiting for name.
    fsm_mid = _new_fsm(_NewBookingRepo())
    fsm_mid.handle("I need to book an appointment")
    fsm_mid.handle("1")
    assert fsm_mid.state == "ASK_NAME"
    r2 = fsm_mid.handle("madarchod")
    assert "respectful language" in r2.lower()
    assert fsm_mid.state == "ASK_NAME"


def test_abuse_llm_fallback_when_rule_does_not_match() -> None:
    # "moron" is not in the rule list; fallback classifier should block.
    fsm = _new_fsm(_NewBookingRepo(), enable_llm_polish=True)
    reply = fsm.handle("you are a moron")
    assert "respectful language" in reply.lower()
    assert fsm.state == "INIT"
