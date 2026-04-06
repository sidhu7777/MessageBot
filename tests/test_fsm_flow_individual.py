import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentContext, AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption


def _make_fsm(phone: str = "telegram:111222333") -> AppointmentFSM:
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []
    mock_repo.find_patient_name_by_chat_user_id.return_value = None
    mock_repo.find_patient_name_by_phone_number.return_value = None
    mock_repo.get_doctor_display_name.return_value = "Sanjay"
    mock_repo.default_admin_id.return_value = 1

    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 2
    mock_sched.list_available_dates.return_value = ["2026-03-01", "2026-03-02"]
    mock_sched.list_available_times.return_value = ["10:00", "10:15", "10:30"]
    mock_sched.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Care Clinic", location="MG Road", today_slots=5),
        ClinicOption(clinic_id=2, clinic_name="Sunrise Health", location="KPHB", today_slots=3),
    ]

    fsm = AppointmentFSM(
        llm_client=mock_llm,
        mixed_response_language="en",
        enable_llm_polish=False,
        booking_repository=mock_repo,
        scheduling_repository=mock_sched,
    )
    fsm.state = "INIT"
    fsm.context = AppointmentContext()
    fsm.chat_phone_number = phone
    fsm.doctor_id = 1
    fsm.admin_id = 1
    fsm.clinic_options_cache = [
        {"id": "1", "ordinal": "1", "name": "City Care Clinic", "address": "MG Road", "today_slots": 5},
        {"id": "2", "ordinal": "2", "name": "Sunrise Health", "address": "KPHB", "today_slots": 3},
    ]
    return fsm


def test_fsm_init_to_ask_booking_for() -> None:
    fsm = _make_fsm()
    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        reply = fsm.handle("book appointment")
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "Welcome to Dr. Sanjay clinic." in reply
    assert "Sure, I can help you book an appointment." in reply
    assert "Who is this appointment for?" in reply


def test_fsm_init_menu_option_one_continues_without_second_greeting() -> None:
    fsm = _make_fsm()
    reply = fsm.handle("1")
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "Sure, I can help you book an appointment." in reply
    assert "Who is this appointment for?" in reply
    assert "Welcome to Dr. Sanjay clinic." not in reply


def test_fsm_init_mixed_greeting_with_booking_intent_uses_booking_greeting() -> None:
    fsm = _make_fsm()
    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        reply = fsm.handle("hello, book an appointment for me")
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "Welcome to Dr. Sanjay clinic." in reply
    assert "Sure, I can help you book an appointment." in reply
    assert "Who is this appointment for?" in reply


def test_fsm_ask_booking_for_to_ask_name_for_other() -> None:
    fsm = _make_fsm()
    fsm.state = "ASK_BOOKING_FOR"
    fsm.handle("2")
    assert fsm.state == "ASK_NAME"
    assert fsm.booking_for_self is False


def test_fsm_ask_name_to_ask_phone() -> None:
    fsm = _make_fsm()
    fsm.state = "ASK_NAME"
    fsm.booking_for_self = False
    fsm.handle("Vineeth Raja")
    assert fsm.state == "ASK_PHONE"
    assert fsm.context.patient_name == "Vineeth Raja"


def test_fsm_ask_phone_to_ask_clinic() -> None:
    fsm = _make_fsm()
    fsm.state = "ASK_PHONE"
    fsm.booking_for_self = False
    fsm.context.patient_name = "Vineeth Raja"
    fsm.handle("9876543210")
    assert fsm.state == "ASK_CLINIC"
    assert fsm.context.phone_number == "9876543210"


def test_fsm_clinic_date_time_confirm_to_completed() -> None:
    fsm = _make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.context.phone_number = "9876543210"

    fsm.handle("1")
    assert fsm.state == "ASK_DATE"

    fsm.handle("1")
    assert fsm.state == "ASK_TIME"

    fsm.time_options_cache = ["10:00", "10:15", "10:30"]
    fsm.handle("10:00")
    assert fsm.state == "CONFIRM"

    save_result = MagicMock()
    save_result.ok = True
    save_result.appointment_id = 99
    save_result.queue_number = 5
    fsm.booking_repository.save_confirmed_appointment.return_value = save_result

    fsm.handle("1")
    assert fsm.state == "COMPLETED"
