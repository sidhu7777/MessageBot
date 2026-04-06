from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext


class _StubLLM:
    def generate(self, system: str, user: str) -> str:
        return "BOOK_APPOINTMENT"


class _Clinic:
    def __init__(self, clinic_id: int, clinic_name: str, location: str, today_slots: int = 3) -> None:
        self.clinic_id = clinic_id
        self.clinic_name = clinic_name
        self.location = location
        self.today_slots = today_slots


class _SchedulingRepo:
    def default_doctor_id(self, admin_id=None):
        return 1

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [
            _Clinic(1, "City Care Clinic", "MG Road"),
            _Clinic(2, "Sunrise Health Center", "KPHB"),
            _Clinic(3, "Green Valley Clinic", "Gachibowli"),
        ][:limit]

    def doctor_accept_days(self, doctor_id: int, admin_id=None):
        return 2

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 3):
        return ["2026-03-03"][:limit]

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 60):
        return ["10:00", "10:30", "11:00"][:limit]


def _new_fsm() -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=_StubLLM(),
        scheduling_repository=_SchedulingRepo(),
    )
    fsm.admin_id = 1
    fsm.doctor_id = 1
    fsm.context = AppointmentContext(
        patient_name="Test User",
        phone_number="9999999999",
    )
    return fsm


def test_booking_for_accepts_english_hinglish_hindi_number_words() -> None:
    fsm = _new_fsm()
    fsm.state = "ASK_BOOKING_FOR"
    reply = fsm.handle("one")
    assert fsm.state == "ASK_NAME"
    assert "booking for self" in reply.lower()

    fsm = _new_fsm()
    fsm.state = "ASK_BOOKING_FOR"
    reply = fsm.handle("ek")
    assert fsm.state == "ASK_NAME"
    assert "booking for self" in reply.lower()

    fsm = _new_fsm()
    fsm.state = "ASK_BOOKING_FOR"
    reply = fsm.handle("२")
    assert fsm.state == "ASK_NAME"
    assert "another person" in reply.lower()


def test_clinic_and_confirm_accept_text_numbers_without_breaking_numeric_flow() -> None:
    fsm = _new_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.booking_for_self = False
    reply = fsm.handle("three")
    assert fsm.state == "ASK_DATE"
    assert "please choose appointment date" in reply.lower()

    fsm = _new_fsm()
    fsm.state = "CONFIRM"
    reply = fsm.handle("two")
    assert fsm.state == "ASK_CHANGE_FIELD"
    assert "which detail do you want to change" in reply.lower()
