"""
Test: Known Telegram Patient Phone Auto-Fill
Verifies that:
  - Known Telegram patients have their phone number auto-filled from database
  - Phone number appears in confirmation message
  - No "Contact: None" for known patients

Run: python tests/test_known_telegram_patient_phone_autofill.py
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


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ [PASS] {label}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  ✗ [FAIL] {label}{detail_str}")


def make_telegram_fsm_with_known_patient(
    patient_name: str = "Ghrdftufg",
    patient_phone: str = "919876543210",
    chat_id: str = "8299824956"
) -> AppointmentFSM:
    """Create FSM for a known Telegram patient with phone in database."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    # No active bookings
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []

    # Known patient with phone
    mock_repo.find_patient_name_by_chat_user_id.return_value = patient_name
    mock_repo.find_patient_phone_by_chat_user_id.return_value = patient_phone

    mock_repo.get_doctor_display_name.return_value = "Dr. Sharma"
    mock_repo.default_admin_id.return_value = 1

    # For appointment persistence
    mock_repo.insert_or_update_patient.return_value = 15
    mock_repo.insert_appointment.return_value = 101

    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 3
    mock_sched.list_available_dates.return_value = ["2026-03-06", "2026-03-07"]
    mock_sched.list_available_times.return_value = ["16:00"]
    mock_sched.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=3, clinic_name="Health Plus Clinic", location="Jubilee Hills", today_slots=1),
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
    fsm.chat_phone_number = f"telegram:{chat_id}"
    fsm.doctor_id = 1
    fsm.admin_id = 1
    fsm.clinic_options_cache = [
        {"id": "3", "name": "Health Plus Clinic", "address": "Jubilee Hills", "today_slots": 1}
    ]
    fsm.date_options_cache = ["2026-03-06"]
    return fsm


# ─── Test 1: Phone hydration on init ──────────────────────────────────────────

def test_phone_hydration_on_init():
    """Test that phone is retrieved when patient name is hydrated."""
    print("\n[TEST 1] Phone number hydration on session init")

    fsm = make_telegram_fsm_with_known_patient(
        patient_name="Ghrdftufg",
        patient_phone="919876543210",
        chat_id="8299824956"
    )

    # Trigger initial message to hydrate patient data
    reply = fsm.handle("Hello, I need to book an appointment")

    check("known_patient_name is set", fsm.known_patient_name == "Ghrdftufg",
          f"Got: {fsm.known_patient_name}")
    check("known_patient_phone is set", fsm.known_patient_phone == "919876543210",
          f"Got: {fsm.known_patient_phone}")
    check("state transitions from INIT", fsm.state != "INIT",
          f"State: {fsm.state}")


# ─── Test 2: Phone auto-fill when booking for self ────────────────────────────

def test_phone_autofill_booking_for_self():
    """Test that phone is auto-filled when known patient books for self."""
    print("\n[TEST 2] Phone auto-fill when booking for self")

    fsm = make_telegram_fsm_with_known_patient(
        patient_name="Ghrdftufg",
        patient_phone="919876543210",
        chat_id="8299824956"
    )

    # Start booking flow
    fsm.handle("Hello, I need to book an appointment")

    # Select "Self" (option 1)
    reply = fsm.handle("1")

    check("context.patient_name is set", fsm.context.patient_name == "Ghrdftufg",
          f"Got: {fsm.context.patient_name}")
    check("context.phone_number is set", fsm.context.phone_number == "919876543210",
          f"Got: {fsm.context.phone_number}")
    check("reply acknowledges phone", "919876543210" in reply or "9876543210" in reply,
          f"reply snippet: {reply[:300]}")


# ─── Test 3: Phone appears in confirmation message ────────────────────────────

def test_phone_in_confirmation_message():
    """Test that phone appears correctly in confirmation (not 'None')."""
    print("\n[TEST 3] Phone appears in confirmation message")

    fsm = make_telegram_fsm_with_known_patient(
        patient_name="Ghrdftufg",
        patient_phone="919876543210",
        chat_id="8299824956"
    )

    # Complete booking flow
    fsm.handle("Hello, I need to book an appointment")  # INIT → ASK_BOOKING_FOR
    fsm.handle("1")  # ASK_BOOKING_FOR → ASK_CLINIC (self)
    fsm.handle("1")  # ASK_CLINIC → ASK_DATE
    fsm.handle("1")  # ASK_DATE → ASK_TIME
    fsm.handle("1")  # ASK_TIME → CONFIRM

    # Check confirmation message
    check("state is CONFIRM", fsm.state == "CONFIRM",
          f"State: {fsm.state}")
    check("context has phone", fsm.context.phone_number == "919876543210",
          f"Got: {fsm.context.phone_number}")

    # Now confirm the appointment - check final message
    reply = fsm.handle("1")  # CONFIRM → COMPLETED

    check("state is COMPLETED", fsm.state == "COMPLETED",
          f"State: {fsm.state}")
    check("reply does NOT contain 'Contact: None'", "Contact: None" not in reply,
          f"reply snippet: {reply[:500]}")
    check("reply contains phone number", "919876543210" in reply or "9876543210" in reply,
          f"reply snippet: {reply[:500]}")


# ─── Test 4: Complete flow like real scenario ─────────────────────────────────

def test_complete_known_patient_flow():
    """Complete flow matching the real log scenario."""
    print("\n[TEST 4] Complete known patient booking flow (real scenario)")

    fsm = make_telegram_fsm_with_known_patient(
        patient_name="Ghrdftufg",
        patient_phone="919876543210",
        chat_id="8299824956"
    )

    print("  Step 1: Initial greeting")
    r1 = fsm.handle("Hello , i need to book an appointment")
    check("hydrated patient name", fsm.known_patient_name == "Ghrdftufg")
    check("hydrated patient phone", fsm.known_patient_phone == "919876543210")

    print("  Step 2: Select 'Self'")
    r2 = fsm.handle("1")
    check("patient name in context", fsm.context.patient_name == "Ghrdftufg")
    check("phone in context after self selection", fsm.context.phone_number == "919876543210")
    check("phone acknowledged in reply", "919876543210" in r2 or "9876543210" in r2,
          f"Reply: {r2[:300]}")

    print("  Step 3: Select clinic")
    r3 = fsm.handle("1")
    check("moved to ASK_DATE or later", fsm.state in ["ASK_DATE", "ASK_TIME"])

    print("  Step 4: Select date")
    r4 = fsm.handle("1")
    check("moved to ASK_TIME or later", fsm.state in ["ASK_TIME", "CONFIRM"])

    print("  Step 5: Select time")
    r5 = fsm.handle("1")
    check("moved to CONFIRM", fsm.state == "CONFIRM")
    check("confirmation has phone", "Contact: " in r5 and "None" not in r5,
          f"Confirmation: {r5[:400]}")

    print("  Step 6: Confirm appointment")
    r6 = fsm.handle("1")
    check("state is COMPLETED", fsm.state == "COMPLETED")
    check("final message has phone", "919876543210" in r6 or "9876543210" in r6)
    check("final message NOT 'Contact: None'", "Contact: None" not in r6,
          f"Final: {r6[:500]}")


def test_known_patient_without_phone_asks_for_it():
    """
    Test: Known Telegram patient WITHOUT phone in DB should be asked for phone.
    This is the bug fix scenario.
    """
    print("\n5️⃣  Test: Known patient WITHOUT phone in DB → asks for phone")

    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    # No active bookings
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []

    # Known patient with name but NO phone (NULL in DB)
    mock_repo.find_patient_name_by_chat_user_id.return_value = "Ghrdftufg"
    mock_repo.find_patient_phone_by_chat_user_id.return_value = None  # NULL phone

    # Mock clinic options
    mock_sched.list_clinics.return_value = [
        ClinicOption(clinic_id=3, clinic_name="Health Plus Clinic", location="Main St", today_slots=5)
    ]
    mock_sched.list_available_dates.return_value = ["2026-03-06"]
    mock_sched.list_available_times.return_value = ["15:00"]
    mock_sched.default_doctor_id.return_value = 10
    mock_sched.default_doctor_id_by_username.return_value = 10

    mock_repo.get_doctor_display_name.return_value = "Dr. Test"
    mock_repo.default_admin_id.return_value = 1

    fsm = AppointmentFSM(
        llm_client=mock_llm,
        mixed_response_language="en",
        enable_llm_polish=False,
        booking_repository=mock_repo,
        scheduling_repository=mock_sched,
    )
    fsm.state = "INIT"
    fsm.context = AppointmentContext()
    fsm.chat_phone_number = "telegram:8299824956"  # Telegram format
    fsm.admin_id = 1
    fsm.doctor_id = 10
    fsm.clinic_options_cache = [
        {"id": "3", "name": "Health Plus Clinic", "address": "Main St", "today_slots": 5}
    ]
    
    # Manually trigger hydration since we're testing edge case
    fsm._hydrate_known_patient_name()
    
    check("known_patient_name hydrated", fsm.known_patient_name == "Ghrdftufg")
    check("known_patient_phone is None", fsm.known_patient_phone is None)

    print("  Step 1: User says 'Hello'")
    r1 = fsm.handle("Hello")
    check("state is INIT", fsm.state == "INIT")

    print("  Step 2: User chooses '1' (Book appointment)")
    r2 = fsm.handle("1")
    check("moved to ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR")

    print("  Step 3: User chooses '1' (Self)")
    r3 = fsm.handle("1")
    
    # ✅ KEY TEST: Should go to ASK_PHONE, not ASK_CLINIC
    check("moved to ASK_PHONE (not ASK_CLINIC)", fsm.state == "ASK_PHONE",
          f"Expected ASK_PHONE, got {fsm.state}")
    check("response acknowledges name", "Ghrdftufg" in r3)
    check("response asks for phone", "phone" in r3.lower() or "number" in r3.lower())

    print("  Step 4: User provides phone '9123456789'")
    r4 = fsm.handle("9123456789")
    # Phone extraction adds country code, so check for normalized version
    check("context.phone_number populated", fsm.context.phone_number is not None,
          f"Phone: {fsm.context.phone_number}")
    # The important test: after providing phone, should move forward (ASK_CLINIC or later)
    check("moved forward after phone (not stuck in ASK_PHONE)", 
          fsm.state != "ASK_PHONE",
          f"State: {fsm.state}")


# ─── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("TEST: Known Telegram Patient Phone Auto-Fill")
    print("=" * 70)

    test_phone_hydration_on_init()
    test_phone_autofill_booking_for_self()
    test_phone_in_confirmation_message()
    test_complete_known_patient_flow()
    test_known_patient_without_phone_asks_for_it()

    print("\n" + "=" * 70)
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    print("=" * 70)

    if FAIL > 0:
        sys.exit(1)
    else:
        print("\n✓ All tests passed!")
        sys.exit(0)
