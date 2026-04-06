"""
REQ-005: Known Patient vs Unknown Patient Flow
Verifies that:
  - Known patient (previously booked): personalized greeting + name/phone auto-filled
  - Unknown patient (first time): generic greeting + goes through all steps

Run: python tests/req_005_known_unknown_patient.py
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


def make_fsm(patient_name: str | None, phone: str = "telegram:999111222") -> AppointmentFSM:
    """Create FSM where DB lookup returns patient_name (or None for unknown)."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    # No active bookings so we go to fresh booking
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []

    # Known vs Unknown
    mock_repo.find_patient_name_by_chat_user_id.return_value = patient_name
    mock_repo.find_patient_name_by_phone_number.return_value = patient_name

    mock_repo.get_doctor_display_name.return_value = "Dr. Sanjay"
    mock_repo.default_admin_id.return_value = 1

    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 2
    mock_sched.list_available_dates.return_value = ["2026-03-01", "2026-03-02"]
    mock_sched.list_available_times.return_value = ["10:00", "10:15", "10:30"]
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
    fsm.clinic_options_cache = [
        {"id": "1", "name": "City Care Clinic", "address": "MG Road", "today_slots": 5}
    ]
    return fsm


# ─── Test 1: Known patient greeting ───────────────────────────────────────────

def test_known_patient_greeting():
    print("\n[TEST] Known patient sees personalized greeting with name")

    fsm = make_fsm(patient_name="Vineeth Raja")

    reply = fsm.handle("/start")  # Telegram /start command triggers greeting

    check("reply contains patient name", "Vineeth Raja" in reply,
          f"reply was: {reply[:200]}")
    check("known_patient_name is set on FSM", fsm.known_patient_name == "Vineeth Raja")
    check("reply contains doctor name", "Dr. Sanjay" in reply or "Sanjay" in reply)


# ─── Test 2: Unknown patient greeting ─────────────────────────────────────────

def test_unknown_patient_greeting():
    print("\n[TEST] Unknown patient sees generic welcome (no name)")

    fsm = make_fsm(patient_name=None)

    reply = fsm.handle("/start")

    check("generic welcome shown", "Welcome to Dr. Sanjay clinic" in reply or "Dr. Sanjay" in reply,
          f"reply: {reply[:200]}")
    check("known_patient_name is NOT set", fsm.known_patient_name is None)
    # Should NOT contain placeholder like 'None' or patient name slot
    check("reply does not say 'None'", "None" not in reply)


# ─── Test 3: Known patient selects 'self' → skips ASK_NAME + ASK_PHONE ────────

def test_known_patient_self_skips_name_and_phone():
    print("\n[TEST] Known patient selects '1' (self) → name+phone auto-filled, skip to ASK_CLINIC")

    fsm = make_fsm(patient_name="Vineeth Raja", phone="telegram:999111222")
    # Trigger greeting to set known_patient_name
    fsm.handle("/start")

    check("known_patient_name set after start", fsm.known_patient_name == "Vineeth Raja")

    # Now request booking
    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        fsm.handle("book appointment")

    check("state is ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR")

    # Choose self → should auto-fill name and skip to clinic (Telegram, no phone)
    reply = fsm.handle("1")

    check("state is ASK_CLINIC (name+phone skipped)", fsm.state == "ASK_CLINIC",
          f"actual state: {fsm.state}")
    check("patient name auto-filled", fsm.context.patient_name == "Vineeth Raja")
    check("reply contains name acknowledgment", "Vineeth Raja" in reply)


# ─── Test 4: Unknown patient must go through ASK_NAME ─────────────────────────

def test_unknown_patient_must_enter_name():
    print("\n[TEST] Unknown patient must enter name (not skipped)")

    fsm = make_fsm(patient_name=None)
    fsm.handle("/start")

    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        fsm.handle("book appointment")

    fsm.handle("2")  # booking for another person also goes to ASK_NAME

    check("unknown patient lands on ASK_NAME", fsm.state == "ASK_NAME")


# ─── Test 5: Known patient books 'for another person' → must enter name ────────

def test_known_patient_other_must_enter_name():
    print("\n[TEST] Known patient selects 'another person' → still goes through ASK_NAME")

    fsm = make_fsm(patient_name="Vineeth Raja")
    fsm.handle("/start")

    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("BOOK_APPOINTMENT", "en")):
        fsm.handle("book appointment")

    reply = fsm.handle("2")  # booking for another person

    check("state is ASK_NAME for another person", fsm.state == "ASK_NAME")
    check("booking_for_self is False", fsm.booking_for_self is False)


# ─── Test 6: Known patient reset → greeting does DB lookup again ───────────────

def test_known_patient_name_cleared_on_reset():
    print("\n[TEST] After cancelled/reset, known_patient_name is cleared")

    fsm = make_fsm(patient_name="Vineeth Raja")
    fsm.handle("/start")
    check("known_patient_name set after start", fsm.known_patient_name == "Vineeth Raja")

    # Cancel the flow
    fsm.handle("stop")

    check("state is CANCELLED after end", fsm.state == "CANCELLED")
    check("known_patient_name cleared on reset", fsm.known_patient_name is None)


# ─── Test 7: WhatsApp known patient (non-Telegram) ───────────────────────────

def test_whatsapp_known_patient():
    print("\n[TEST] WhatsApp known patient uses phone lookup (not chat_user_id)")

    fsm = make_fsm(patient_name="Rajesh Kumar", phone="whatsapp:+919876543210")

    with patch("src.fsm.appointment_fsm.route_initial_decision", return_value=("GREETING", "en")):
        reply = fsm.handle("hello")

    # Should try phone-based lookup
    fsm.booking_repository.find_patient_name_by_phone_number.assert_called()
    check("WhatsApp patient name shown in greeting", "Rajesh Kumar" in reply,
          f"reply: {reply[:200]}")


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-005: Known Patient vs Unknown Patient Flow")
    print("=" * 60)

    test_known_patient_greeting()
    test_unknown_patient_greeting()
    test_known_patient_self_skips_name_and_phone()
    test_unknown_patient_must_enter_name()
    test_known_patient_other_must_enter_name()
    test_known_patient_name_cleared_on_reset()
    test_whatsapp_known_patient()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
