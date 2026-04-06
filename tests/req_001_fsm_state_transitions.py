"""
REQ-001: FSM State Transitions
Verifies that AppointmentFSM moves through correct states in order.
No real DB or LLM needed — uses mocks.
Run: python tests/req_001_fsm_state_transitions.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{detail_str}")


def make_fsm(phone: str = "telegram:111222333") -> AppointmentFSM:
    """Create FSM with all external deps mocked out."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    # No existing bookings → go straight to new booking
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []
    mock_repo.find_patient_name_by_chat_user_id.return_value = None
    mock_repo.find_patient_name_by_phone_number.return_value = None
    mock_repo.get_doctor_display_name.return_value = "Sanjay"
    mock_repo.default_admin_id.return_value = 1

    # Provide one clinic, one date, and a set of time slots
    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 2
    mock_sched.list_available_dates.return_value = ["2026-03-01", "2026-03-02"]
    mock_sched.list_available_times.return_value = [
        "10:00", "10:15", "10:30", "11:00", "11:15"
    ]
    mock_sched.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Care Clinic", location="MG Road, Hyderabad", today_slots=5),
        ClinicOption(clinic_id=2, clinic_name="Sunrise Health", location="KPHB, Hyderabad", today_slots=3),
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

    # Patch clinic_options_cache directly so ASK_CLINIC doesn't need DB call
    fsm.clinic_options_cache = [
        {"id": "1", "ordinal": "1", "name": "City Care Clinic", "address": "MG Road, Hyderabad", "today_slots": 5},
        {"id": "2", "ordinal": "2", "name": "Sunrise Health", "address": "KPHB, Hyderabad", "today_slots": 3},
    ]

    return fsm


def test_init_to_ask_booking_for():
    print("\n[TEST] INIT -> ASK_BOOKING_FOR on 'book appointment'")
    fsm = make_fsm()
    check("state starts at INIT", fsm.state == "INIT")

    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        reply = fsm.handle("book appointment")

    check("state is now ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR")
    check("reply contains booking choice", "Who is this appointment for" in reply or "appointment for" in reply.lower())


def test_booking_for_self_known_patient():
    print("\n[TEST] ASK_BOOKING_FOR '2' (another) -> ASK_NAME")
    fsm = make_fsm()
    fsm.state = "ASK_BOOKING_FOR"

    reply = fsm.handle("2")

    check("state is ASK_NAME after choice 2", fsm.state == "ASK_NAME")
    check("booking_for_self is False", fsm.booking_for_self is False)


def test_ask_name_advances_to_ask_phone():
    print("\n[TEST] ASK_NAME valid name -> ASK_PHONE")
    fsm = make_fsm()
    fsm.state = "ASK_NAME"
    fsm.booking_for_self = False

    reply = fsm.handle("Vineeth Raja")

    check("state is ASK_PHONE", fsm.state == "ASK_PHONE")
    check("patient name stored", fsm.context.patient_name == "Vineeth Raja")


def test_ask_phone_advances_to_ask_clinic():
    print("\n[TEST] ASK_PHONE valid number -> ASK_CLINIC")
    fsm = make_fsm()
    fsm.state = "ASK_PHONE"
    fsm.booking_for_self = False
    fsm.context.patient_name = "Vineeth Raja"

    reply = fsm.handle("9876543210")

    check("state is ASK_CLINIC", fsm.state == "ASK_CLINIC")
    check("phone stored", fsm.context.phone_number == "9876543210")


def test_ask_clinic_advances_to_ask_date():
    print("\n[TEST] ASK_CLINIC choice '1' -> ASK_DATE")
    fsm = make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.context.phone_number = "9876543210"

    reply = fsm.handle("1")

    check("state is ASK_DATE", fsm.state == "ASK_DATE")
    check("clinic_id stored", fsm.context.clinic_id is not None)


def test_ask_date_advances_to_ask_time():
    print("\n[TEST] ASK_DATE choice '1' -> ASK_TIME")
    fsm = make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.context.phone_number = "9876543210"
    fsm.handle("1")  # pick clinic -> goes to ASK_DATE

    reply = fsm.handle("1")  # pick first date

    check("state is ASK_TIME", fsm.state == "ASK_TIME")
    check("date stored", fsm.context.appointment_date is not None)


def test_ask_time_advances_to_confirm():
    print("\n[TEST] ASK_TIME exact available slot -> CONFIRM")
    fsm = make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.context.phone_number = "9876543210"
    fsm.handle("1")  # clinic
    fsm.handle("1")  # date -> ASK_TIME

    # Force time_options_cache so we can pick an exact slot
    fsm.time_options_cache = ["10:00", "10:15", "10:30", "11:00", "11:15"]

    reply = fsm.handle("10:00")

    check("state is CONFIRM", fsm.state == "CONFIRM")
    check("time stored", fsm.context.appointment_time == "10:00")


def test_confirm_yes_completes():
    print("\n[TEST] CONFIRM '1' (yes) -> COMPLETED")
    fsm = make_fsm()
    fsm.state = "CONFIRM"
    fsm.context.patient_name = "Vineeth Raja"
    fsm.context.phone_number = "9876543210"
    fsm.context.clinic_id = "1"
    fsm.context.clinic_name = "City Care Clinic"
    fsm.context.clinic_address = "MG Road"
    fsm.context.appointment_date = "2026-03-01"
    fsm.context.appointment_time = "10:00"

    # Mock save
    save_result = MagicMock()
    save_result.ok = True
    save_result.appointment_id = 99
    save_result.queue_number = 5
    fsm.booking_repository.save_confirmed_appointment.return_value = save_result

    reply = fsm.handle("1")

    check("state is COMPLETED", fsm.state == "COMPLETED")
    check("reply has booking confirmation", "confirmed" in reply.lower() or "appointment" in reply.lower())


def test_invalid_name_stays_in_ask_name():
    print("\n[TEST] ASK_NAME garbage input -> returns invalid_name message")
    fsm = make_fsm()
    fsm.state = "ASK_NAME"
    fsm.booking_for_self = False

    # "OTHER" routes to INIT; "BOOK_APPOINTMENT"/"UNCLEAR" stays with invalid_name
    # Patch to a non-routing intent so invalid_name error is returned
    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        reply = fsm.handle("??##!!")

    # With BOOK_APPOINTMENT routing from ASK_NAME, state goes to INIT (re-route)
    # Actually: extract_name("??##!!") fails, then route_initial_decision returns BOOK_APPOINTMENT
    # which is not in {"GENERAL_QUERY","OTHER","CHECK_AVAILABILITY"} → returns invalid_name
    check("state stays at ASK_NAME", fsm.state == "ASK_NAME",
          f"actual state: {fsm.state}")
    check("reply has invalid_name hint",
          "valid name" in reply.lower() or "name" in reply.lower(),
          f"reply: {reply[:200]}")


def test_go_back_from_ask_name():
    print("\n[TEST] '0' from ASK_NAME -> ASK_BOOKING_FOR")
    fsm = make_fsm()
    fsm.state = "ASK_NAME"

    reply = fsm.handle("0")

    check("state is ASK_BOOKING_FOR after go-back", fsm.state == "ASK_BOOKING_FOR")


def test_abuse_blocked_after_two_warnings():
    print("\n[TEST] Abusive x2 -> abuse_blocked, returns empty string")
    fsm = make_fsm()
    fsm.state = "INIT"

    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("OTHER", "en")):
        fsm.handle("fuck this")
        fsm.handle("fuck you")
        reply = fsm.handle("hello")

    check("abuse_blocked after 2 warnings", fsm.context.abuse_blocked is True)
    check("blocked user gets no response", reply == "")


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-001: FSM State Transitions")
    print("=" * 60)

    test_init_to_ask_booking_for()
    test_booking_for_self_known_patient()
    test_ask_name_advances_to_ask_phone()
    test_ask_phone_advances_to_ask_clinic()
    test_ask_clinic_advances_to_ask_date()
    test_ask_date_advances_to_ask_time()
    test_ask_time_advances_to_confirm()
    test_confirm_yes_completes()
    test_invalid_name_stays_in_ask_name()
    test_go_back_from_ask_name()
    test_abuse_blocked_after_two_warnings()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
