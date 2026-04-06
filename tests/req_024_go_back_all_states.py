"""
REQ-024: Go-Back ("0") Navigation — All FSM States
====================================================
Verifies that pressing "0" from every navigable state returns to the
correct previous state AND renders the expected prompt.

Special regression guard:
  CONFIRM → "0" must show the time-slot picker, NOT "No available time slots".
  (Bug: time_options_cache / time_hour_options_cache were not persisted in the
   session snapshot, so _initial_time_prompt() received empty caches.)

Run: python tests/req_024_go_back_all_states.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption

PASS = 0
FAIL = 0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


_TIME_SLOTS = ["10:00", "10:30", "11:00", "11:30", "12:00"]
_DATES = ["2026-03-10", "2026-03-11", "2026-03-12"]


def make_fsm() -> AppointmentFSM:
    """FSM with all external deps mocked; suitable for all go-back tests."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    # No existing bookings → new booking path
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []
    mock_repo.find_patient_name_by_chat_user_id.return_value = None
    mock_repo.find_patient_name_by_phone_number.return_value = None
    mock_repo.get_doctor_display_name.return_value = "Sanjay"
    mock_repo.default_admin_id.return_value = 1

    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 2
    mock_sched.list_available_dates.return_value = _DATES
    mock_sched.list_available_times.return_value = list(_TIME_SLOTS)
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
    fsm.chat_phone_number = "telegram:111222333"
    fsm.doctor_id = 1
    fsm.admin_id = 1

    # Pre-populate clinic cache so ASK_CLINIC doesn't need a live DB call
    fsm.clinic_options_cache = [
        {"id": "1", "ordinal": "1", "name": "City Care Clinic", "address": "MG Road", "today_slots": 5},
        {"id": "2", "ordinal": "2", "name": "Sunrise Health", "address": "KPHB", "today_slots": 3},
    ]
    return fsm


def _set_full_context(fsm: AppointmentFSM) -> None:
    """Populate context so any state can render a summary without missing keys."""
    fsm.context.patient_name = "TestUser"
    fsm.context.phone_number = "9000000001"
    fsm.context.clinic_id = "1"
    fsm.context.clinic_name = "City Care Clinic"
    fsm.context.clinic_address = "MG Road"
    fsm.context.appointment_date = "2026-03-10"
    fsm.context.appointment_time = "10:00"
    fsm.booking_for_self = False


# ---------------------------------------------------------------------------
# Forward-flow helper — walks the FSM from INIT all the way to CONFIRM
# ---------------------------------------------------------------------------

def _reach_confirm(fsm: AppointmentFSM) -> None:
    """Drive the FSM to CONFIRM state, fully populating the time caches."""
    fsm.state = "ASK_BOOKING_FOR"
    # Choose "2" = another person → ASK_NAME
    fsm.handle("2")
    assert fsm.state == "ASK_NAME", f"Expected ASK_NAME, got {fsm.state}"

    # Enter name → ASK_PHONE
    fsm.handle("TestUser")
    assert fsm.state == "ASK_PHONE", f"Expected ASK_PHONE, got {fsm.state}"

    # Enter phone → ASK_CLINIC
    fsm.handle("9000000001")
    assert fsm.state == "ASK_CLINIC", f"Expected ASK_CLINIC, got {fsm.state}"

    # Pick clinic 1 → ASK_DATE
    fsm.handle("1")
    assert fsm.state == "ASK_DATE", f"Expected ASK_DATE, got {fsm.state}"

    # Pick date 1 → ASK_TIME (loads time_options_cache)
    fsm.handle("1")
    assert fsm.state == "ASK_TIME", f"Expected ASK_TIME, got {fsm.state}"

    # Type exact slot → CONFIRM
    fsm.handle("10:00")
    assert fsm.state == "CONFIRM", f"Expected CONFIRM, got {fsm.state}"


# ---------------------------------------------------------------------------
# Tests — Go-back navigation
# ---------------------------------------------------------------------------

def test_goback_ask_booking_for_to_init():
    print("\n[TEST 1] ASK_BOOKING_FOR + '0' → INIT")
    fsm = make_fsm()
    fsm.state = "ASK_BOOKING_FOR"

    reply = fsm.handle("0")

    check("state becomes INIT", fsm.state == "INIT",
          f"actual: {fsm.state}")
    check("reply is non-empty", bool(reply.strip()))


def test_goback_ask_name_to_ask_booking_for():
    print("\n[TEST 2] ASK_NAME + '0' → ASK_BOOKING_FOR")
    fsm = make_fsm()
    fsm.state = "ASK_NAME"
    fsm.booking_for_self = False

    reply = fsm.handle("0")

    check("state becomes ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR",
          f"actual: {fsm.state}")
    check("reply mentions booking choice",
          any(w in reply.lower() for w in ["appointment", "booking", "self", "another"]),
          f"reply: {reply[:120]}")


def test_goback_ask_phone_to_ask_name():
    print("\n[TEST 3] ASK_PHONE + '0' → ASK_NAME")
    fsm = make_fsm()
    fsm.state = "ASK_PHONE"
    fsm.booking_for_self = False

    reply = fsm.handle("0")

    check("state becomes ASK_NAME", fsm.state == "ASK_NAME",
          f"actual: {fsm.state}")
    check("reply asks for name",
          any(w in reply.lower() for w in ["name", "patient", "full"]),
          f"reply: {reply[:120]}")


def test_goback_ask_clinic_to_ask_phone():
    print("\n[TEST 4] ASK_CLINIC + '0' → ASK_PHONE (normal path)")
    fsm = make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.booking_for_self = False  # normal path — phone step was visited

    reply = fsm.handle("0")

    check("state becomes ASK_PHONE", fsm.state == "ASK_PHONE",
          f"actual: {fsm.state}")
    check("reply asks for phone",
          any(w in reply.lower() for w in ["phone", "number", "contact", "mobile"]),
          f"reply: {reply[:120]}")


def test_goback_ask_clinic_known_patient_to_ask_booking_for():
    print("\n[TEST 5] ASK_CLINIC + '0' → ASK_BOOKING_FOR (known self-booking patient)")
    fsm = make_fsm()
    fsm.state = "ASK_CLINIC"
    fsm.booking_for_self = True
    fsm.known_patient_name = "KnownPatient"

    reply = fsm.handle("0")

    check("state becomes ASK_BOOKING_FOR for known self patient",
          fsm.state == "ASK_BOOKING_FOR",
          f"actual: {fsm.state}")
    check("reply mentions booking-for choice",
          any(w in reply.lower() for w in ["self", "another", "appointment"]),
          f"reply: {reply[:120]}")


def test_goback_ask_date_to_ask_clinic():
    print("\n[TEST 6] ASK_DATE + '0' → ASK_CLINIC")
    fsm = make_fsm()
    fsm.state = "ASK_DATE"
    _set_full_context(fsm)

    reply = fsm.handle("0")

    check("state becomes ASK_CLINIC", fsm.state == "ASK_CLINIC",
          f"actual: {fsm.state}")
    check("reply lists clinics",
          any(w in reply.lower() for w in ["clinic", "choose", "city care", "sunrise"]),
          f"reply: {reply[:120]}")


def test_goback_ask_time_to_ask_date():
    print("\n[TEST 7] ASK_TIME + '0' → ASK_DATE")
    fsm = make_fsm()
    fsm.state = "ASK_TIME"
    _set_full_context(fsm)

    reply = fsm.handle("0")

    check("state becomes ASK_DATE", fsm.state == "ASK_DATE",
          f"actual: {fsm.state}")
    check("reply lists dates",
          any(w in reply.lower() for w in ["date", "choose", "2026", "today"]),
          f"reply: {reply[:120]}")


def test_goback_confirm_to_ask_time_shows_slots():
    """
    KEY BUG REGRESSION TEST
    -----------------------
    CONFIRM + "0" must:
      1. Move state back to ASK_TIME
      2. Show time slots — NOT "No available time slots for this date"

    The bug was that time_options_cache / time_hour_options_cache were not
    persisted in the session snapshot, so after session reload the caches
    were empty and _initial_time_prompt() returned "no_time_available".

    Fix: caches are now saved to the snapshot AND _handle_go_back re-loads
    them from DB when they are empty.
    """
    print("\n[TEST 8] CONFIRM + '0' → ASK_TIME (must show slots, not 'No available')")
    fsm = make_fsm()

    # Walk through the full forward flow so caches are properly populated
    _reach_confirm(fsm)

    # Verify caches are loaded before pressing 0
    check("time_options_cache populated before going back",
          len(fsm.time_options_cache) > 0,
          f"cache: {fsm.time_options_cache}")
    check("time_hour_options_cache populated",
          len(fsm.time_hour_options_cache) > 0,
          f"hour_cache: {fsm.time_hour_options_cache}")

    # Simulate session reload: clear caches as if reloaded from old Redis snapshot
    # WITHOUT our fix (to prove the fix is needed), then restore them as the fix does
    # Actually — test BOTH paths:

    # Path A: caches intact (normal — no reload between turns)
    reply_a = fsm.handle("0")
    check("[PathA] state becomes ASK_TIME", fsm.state == "ASK_TIME",
          f"actual: {fsm.state}")
    check("[PathA] reply does NOT say 'no available time slots'",
          "no available time slots" not in reply_a.lower(),
          f"reply: {reply_a[:200]}")
    check("[PathA] reply shows a time slot",
          any(t in reply_a for t in ("AM", "PM", "10:", "11:", "12:", "am", "pm")),
          f"reply: {reply_a[:200]}")

    # Path B: caches wiped (simulates old session snapshot lacking cache fields)
    #         _handle_go_back must re-load from DB via _load_time_options()
    fsm2 = make_fsm()
    _reach_confirm(fsm2)
    # Wipe the caches to simulate stale snapshot
    fsm2.time_options_cache = []
    fsm2.time_hour_options_cache = []
    fsm2.time_slot_options_cache = []
    fsm2.time_window_labels_cache = []

    reply_b = fsm2.handle("0")
    check("[PathB] state becomes ASK_TIME even with empty cache",
          fsm2.state == "ASK_TIME",
          f"actual: {fsm2.state}")
    check("[PathB] reply does NOT say 'no available time slots'",
          "no available time slots" not in reply_b.lower(),
          f"reply: {reply_b[:200]}")
    check("[PathB] scheduling_repository.list_available_times was called",
          fsm2.scheduling_repository.list_available_times.called,
          "DB was not called to re-load slots")
    check("[PathB] reply shows a time slot after re-load",
          any(t in reply_b for t in ("AM", "PM", "10:", "11:", "12:", "am", "pm")),
          f"reply: {reply_b[:200]}")


def test_goback_ask_availability_details_to_init():
    print("\n[TEST 9] ASK_AVAILABILITY_DETAILS + '0' → ASK_AVAILABILITY_DATE (date picker)")
    fsm = make_fsm()
    fsm.state = "ASK_AVAILABILITY_DETAILS"
    fsm.context.availability_date = "2026-03-10"
    fsm.context.availability_doctor = "Sanjay"

    reply = fsm.handle("0")

    check("state becomes ASK_AVAILABILITY_DATE", fsm.state == "ASK_AVAILABILITY_DATE",
          f"actual: {fsm.state}")
    check("availability_date cleared", fsm.context.availability_date is None)
    check("reply shows date menu", "1." in reply and "availability" in reply.lower(),
          f"reply: {reply[:120]}")


def test_goback_ask_change_field_to_confirm():
    print("\n[TEST 10] ASK_CHANGE_FIELD + '0' → CONFIRM")
    fsm = make_fsm()
    fsm.state = "ASK_CHANGE_FIELD"
    _set_full_context(fsm)

    reply = fsm.handle("0")

    check("state becomes CONFIRM", fsm.state == "CONFIRM",
          f"actual: {fsm.state}")
    check("reply shows confirm summary",
          any(w in reply.lower() for w in ["confirm", "name", "date", "time"]),
          f"reply: {reply[:120]}")


# ---------------------------------------------------------------------------
# Full flow test: forward walk INIT → CONFIRM, then full backward walk
# ---------------------------------------------------------------------------

def test_full_forward_flow():
    print("\n[TEST 11] Full forward flow: INIT → CONFIRM")
    fsm = make_fsm()

    # INIT → ASK_BOOKING_FOR (booking intent)
    from unittest.mock import patch
    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        r = fsm.handle("book appointment")
    check("INIT → ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR",
          f"actual: {fsm.state}")

    # ASK_BOOKING_FOR → ASK_NAME (another person)
    fsm.handle("2")
    check("ASK_BOOKING_FOR → ASK_NAME", fsm.state == "ASK_NAME",
          f"actual: {fsm.state}")

    # ASK_NAME → ASK_PHONE
    fsm.handle("TestPatient")
    check("ASK_NAME → ASK_PHONE", fsm.state == "ASK_PHONE",
          f"actual: {fsm.state}")

    # ASK_PHONE → ASK_CLINIC
    fsm.handle("9876543210")
    check("ASK_PHONE → ASK_CLINIC", fsm.state == "ASK_CLINIC",
          f"actual: {fsm.state}")

    # ASK_CLINIC → ASK_DATE
    fsm.handle("1")
    check("ASK_CLINIC → ASK_DATE", fsm.state == "ASK_DATE",
          f"actual: {fsm.state}")

    # ASK_DATE → ASK_TIME
    fsm.handle("1")
    check("ASK_DATE → ASK_TIME", fsm.state == "ASK_TIME",
          f"actual: {fsm.state}")
    check("time_options_cache loaded", len(fsm.time_options_cache) > 0,
          f"cache: {fsm.time_options_cache}")

    # ASK_TIME → CONFIRM
    fsm.handle("10:00")
    check("ASK_TIME → CONFIRM", fsm.state == "CONFIRM",
          f"actual: {fsm.state}")
    check("appointment_time stored", fsm.context.appointment_time == "10:00",
          f"actual: {fsm.context.appointment_time}")


def test_full_backward_walk():
    print("\n[TEST 12] Full backward walk: CONFIRM → ... → INIT")
    fsm = make_fsm()
    _reach_confirm(fsm)

    # CONFIRM → ASK_TIME
    r = fsm.handle("0")
    check("CONFIRM → ASK_TIME", fsm.state == "ASK_TIME",
          f"actual: {fsm.state}")
    check("slot prompt shown (not no-slots error)",
          "no available time slots" not in r.lower(),
          f"reply: {r[:160]}")

    # ASK_TIME → ASK_DATE
    r = fsm.handle("0")
    check("ASK_TIME → ASK_DATE", fsm.state == "ASK_DATE",
          f"actual: {fsm.state}")

    # ASK_DATE → ASK_CLINIC
    r = fsm.handle("0")
    check("ASK_DATE → ASK_CLINIC", fsm.state == "ASK_CLINIC",
          f"actual: {fsm.state}")

    # ASK_CLINIC → ASK_PHONE (not known self patient)
    r = fsm.handle("0")
    check("ASK_CLINIC → ASK_PHONE", fsm.state == "ASK_PHONE",
          f"actual: {fsm.state}")

    # ASK_PHONE → ASK_NAME
    r = fsm.handle("0")
    check("ASK_PHONE → ASK_NAME", fsm.state == "ASK_NAME",
          f"actual: {fsm.state}")

    # ASK_NAME → ASK_BOOKING_FOR
    r = fsm.handle("0")
    check("ASK_NAME → ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR",
          f"actual: {fsm.state}")

    # ASK_BOOKING_FOR → INIT
    r = fsm.handle("0")
    check("ASK_BOOKING_FOR → INIT", fsm.state == "INIT",
          f"actual: {fsm.state}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("REQ-024: Go-Back Navigation — All FSM States")
    print("=" * 70)

    test_goback_ask_booking_for_to_init()
    test_goback_ask_name_to_ask_booking_for()
    test_goback_ask_phone_to_ask_name()
    test_goback_ask_clinic_to_ask_phone()
    test_goback_ask_clinic_known_patient_to_ask_booking_for()
    test_goback_ask_date_to_ask_clinic()
    test_goback_ask_time_to_ask_date()
    test_goback_confirm_to_ask_time_shows_slots()
    test_goback_ask_availability_details_to_init()
    test_goback_ask_change_field_to_confirm()
    test_full_forward_flow()
    test_full_backward_walk()

    print("\n" + "=" * 70)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 70)
    sys.exit(0 if FAIL == 0 else 1)
