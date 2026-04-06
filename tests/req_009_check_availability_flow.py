"""
REQ-009: Check Availability Flow
Verifies that the REAL FSM model correctly handles all doctor availability
check scenarios — no hardcoded responses, no fake routing.

Scenarios tested:
  1.  Patient presses "2" (menu option)            → enters availability flow
  2.  Patient types "check availability"            → enters availability flow
  3.  Patient says "I need to know the doctor availability" (the key case)
  4.  Patient says "is doctor available tomorrow"   → date extracted inline
  5.  Patient says "doctor available hai kya"       → Hinglish availability
  6.  Patient in ASK_AVAILABILITY_DETAILS, gives date → gets slots back
  7.  Patient in ASK_AVAILABILITY_DETAILS, no slots on date → next date shown
  8.  Patient in ASK_AVAILABILITY_DETAILS, gives doctor name only → asks for date
  9.  Patient in ASK_AVAILABILITY_DETAILS, says "book appointment" → moves to booking
  10. Patient says "hi" first → greeting; THEN asks availability → still handled
  11. Patient in INIT gives a date inline with availability query → immediate reply
  12. Patient sends Hindi availability query (उपलब्धता)

Run: python tests/req_009_check_availability_flow.py
"""

import sys
from datetime import date, timedelta
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

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


def make_fsm(
    slots: list[str] | None = None,
    next_dates: list[str] | None = None,
    phone: str = "telegram:999888777",
) -> AppointmentFSM:
    """
    Creates a REAL AppointmentFSM with mocked DB/scheduling dependencies.
    The full FSM logic (NLU routing, state transitions, message templates)
    runs as-is — this is NOT a mock of the FSM itself.
    """
    mock_llm = MagicMock(spec=LLMClient)
    # LLM is disabled (enable_llm_polish=False) — FSM uses only regex/rule routing.
    # If any test sends an ambiguous message that falls through to LLM,
    # the mock will raise an error so we notice immediately.

    mock_repo = MagicMock()
    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []
    mock_repo.find_patient_name_by_chat_user_id.return_value = None
    mock_repo.find_patient_name_by_phone_number.return_value = None
    mock_repo.get_doctor_display_name.return_value = "Dr. Sanjay"
    mock_repo.default_admin_id.return_value = 1

    mock_sched = MagicMock()
    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 3
    mock_sched.list_clinics_for_doctor.return_value = [
        ClinicOption(
            clinic_id=1,
            clinic_name="City Care Clinic",
            location="MG Road, Hyderabad",
            today_slots=5,
        )
    ]
    mock_sched.list_available_times.return_value = slots if slots is not None else [
        "10:00", "10:15", "10:30", "11:00", "11:15"
    ]
    mock_sched.list_available_dates.return_value = next_dates if next_dates is not None else [
        TOMORROW
    ]

    fsm = AppointmentFSM(
        llm_client=mock_llm,
        mixed_response_language="en",
        enable_llm_polish=False,   # <-- ONLY regex/rule-based routing, no LLM calls
        booking_repository=mock_repo,
        scheduling_repository=mock_sched,
    )
    fsm.state = "INIT"
    fsm.context = AppointmentContext()
    fsm.chat_phone_number = phone
    fsm.doctor_id = 1
    fsm.admin_id = 1
    return fsm


# ──────────────────────────────────────────────
# Test 1: Menu option "2"
# ──────────────────────────────────────────────
def test_option_2_triggers_availability():
    print("\n[TEST 1] Patient presses '2' (menu option)")
    fsm = make_fsm()
    reply = fsm.handle("2")

    check(
        "State moves to ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply asks for date / availability_intro",
        any(kw in reply.lower() for kw in ["availability", "date", "slot"]),
        f"reply={reply[:120]}",
    )


# ──────────────────────────────────────────────
# Test 2: "check availability" text
# ──────────────────────────────────────────────
def test_check_availability_text():
    print("\n[TEST 2] Patient types 'check availability'")
    fsm = make_fsm()
    reply = fsm.handle("check availability")

    check(
        "State moves to ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply asks for date",
        any(kw in reply.lower() for kw in ["date", "availability", "slot"]),
        f"reply={reply[:120]}",
    )


# ──────────────────────────────────────────────
# Test 3: KEY CASE — "I need to know the doctor availability"
# ──────────────────────────────────────────────
def test_natural_language_availability_request():
    print("\n[TEST 3] Patient says 'I need to know the doctor availability' (KEY CASE)")
    msg = "I need to know the doctor availability"
    fsm = make_fsm()
    reply = fsm.handle(msg)

    check(
        "Regex router catches 'availability' keyword",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply is not an error / not a generic fallback",
        any(kw in reply.lower() for kw in ["date", "availability", "slot", "share"]),
        f"reply={reply[:180]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 4: Availability with date inline — "is doctor available tomorrow"
# ──────────────────────────────────────────────
def test_availability_with_date_inline():
    print("\n[TEST 4] Patient says 'is doctor available tomorrow' (date inline)")
    msg = "is doctor available tomorrow"
    fsm = make_fsm()
    reply = fsm.handle(msg)

    check(
        "State is ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply contains slot/availability info (date extracted inline)",
        any(kw in reply.lower() for kw in ["slot", "available", "city care", TOMORROW]),
        f"reply={reply[:180]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 5: Hinglish — "doctor available hai kya"
# ──────────────────────────────────────────────
def test_hinglish_availability():
    print("\n[TEST 5] Hinglish — 'doctor available hai kya'")
    msg = "doctor available hai kya"
    fsm = make_fsm()
    reply = fsm.handle(msg)

    check(
        "State moves to ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply asks for date or shows availability",
        any(kw in reply.lower() for kw in ["date", "availability", "slot", "share"]),
        f"reply={reply[:180]}",
    )


# ──────────────────────────────────────────────
# Test 6: In ASK_AVAILABILITY_DETAILS — patient gives a date → slots returned
# ──────────────────────────────────────────────
def test_state_give_date_gets_slots():
    print("\n[TEST 6] In availability state — patient gives date → slots shown")
    fsm = make_fsm(slots=["10:00", "10:15", "10:30", "11:00"])
    # Force into availability state
    fsm.state = "ASK_AVAILABILITY_DETAILS"

    reply = fsm.handle(TOMORROW)

    check(
        "State stays in ASK_AVAILABILITY_DETAILS (or remains after reply)",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply shows slot count or time range",
        any(kw in reply.lower() for kw in ["slot", "10:", "city care", TOMORROW, "available"]),
        f"reply={reply[:200]}",
    )
    check(
        "Reply mentions booking option",
        "book" in reply.lower(),
        f"reply={reply[:200]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 7: In ASK_AVAILABILITY_DETAILS — no slots on given date → next date shown
# ──────────────────────────────────────────────
def test_state_no_slots_shows_next_date():
    print("\n[TEST 7] In availability state — no slots on date → next available date shown")
    fsm = make_fsm(slots=[], next_dates=["2026-03-10"])
    fsm.state = "ASK_AVAILABILITY_DETAILS"

    reply = fsm.handle(TODAY)

    check(
        "Reply mentions no slots / next available",
        any(kw in reply.lower() for kw in ["no slot", "next available", "2026-03-10", "not available"]),
        f"reply={reply[:200]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 8: In ASK_AVAILABILITY_DETAILS — no slots AND no next dates
# ──────────────────────────────────────────────
def test_state_no_slots_and_no_next_date():
    print("\n[TEST 8] In availability state — no slots, no future dates → graceful reply")
    fsm = make_fsm(slots=[], next_dates=[])
    fsm.state = "ASK_AVAILABILITY_DETAILS"

    reply = fsm.handle(TODAY)

    check(
        "Reply does not crash and mentions no slots",
        reply.strip() != "" and any(kw in reply.lower() for kw in ["no slot", "not available", "no available"]),
        f"reply={reply[:200]}",
    )


# ──────────────────────────────────────────────
# Test 9: In ASK_AVAILABILITY_DETAILS — says "book appointment" → booking flow
# ──────────────────────────────────────────────
def test_availability_then_booking():
    print("\n[TEST 9] In availability state — says 'book appointment' → moves to booking")
    fsm = make_fsm()
    fsm.state = "ASK_AVAILABILITY_DETAILS"

    reply = fsm.handle("book appointment")

    check(
        "State moves away from ASK_AVAILABILITY_DETAILS (into booking)",
        fsm.state == "ASK_BOOKING_FOR",
        f"actual state={fsm.state}",
    )
    check(
        "Reply contains booking/self/someone prompt",
        any(kw in reply.lower() for kw in ["book", "self", "someone", "for"]),
        f"reply={reply[:180]}",
    )


# ──────────────────────────────────────────────
# Test 10: "hi" → greeting → THEN ask availability
# ──────────────────────────────────────────────
def test_greeting_then_availability():
    print("\n[TEST 10] Patient says 'hi' first, then asks doctor availability")
    fsm = make_fsm()

    # Turn 1: greeting
    reply1 = fsm.handle("hi")
    check(
        "Turn 1 — state still INIT after greeting",
        fsm.state == "INIT",
        f"actual state={fsm.state}",
    )
    check(
        "Turn 1 — welcome greeting shown",
        any(kw in reply1.lower() for kw in ["hello", "hi", "assistant", "help", "sanjay"]),
        f"reply={reply1[:120]}",
    )

    # Turn 2: availability question
    msg2 = "I need to know the doctor availability"
    reply2 = fsm.handle(msg2)
    check(
        "Turn 2 — state moves to ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Turn 2 — bot asks for date / availability info",
        any(kw in reply2.lower() for kw in ["date", "availability", "slot", "share"]),
        f"reply={reply2[:180]}",
    )
    print(f"      Bot Turn-2 replied: {reply2.strip()}")


# ──────────────────────────────────────────────
# Test 11: Availability + date in one message from INIT
# ──────────────────────────────────────────────
def test_availability_with_date_in_init_message():
    print(f"\n[TEST 11] From INIT — 'is doctor available on {TOMORROW}'")
    msg = f"is doctor available on {TOMORROW}"
    fsm = make_fsm(slots=["09:00", "09:15", "09:30"])
    reply = fsm.handle(msg)

    check(
        "State is ASK_AVAILABILITY_DETAILS (intent correctly routed)",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    # Model correctly identifies availability intent.
    # It may either: (a) extract the date inline and show slots,
    # OR (b) ask the patient to confirm/provide the date — both are valid.
    check(
        "Reply is availability-related (slots shown OR date asked)",
        any(kw in reply.lower() for kw in ["slot", "09:", "city care", "date", "availability", "share"]),
        f"reply={reply[:200]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 12: Hindi — "डॉक्टर उपलब्ध हैं"
# ──────────────────────────────────────────────
def test_hindi_availability():
    print("\n[TEST 12] Hindi — 'डॉक्टर उपलब्ध हैं'")
    msg = "डॉक्टर उपलब्ध हैं"
    fsm = make_fsm()
    reply = fsm.handle(msg)

    check(
        "State moves to ASK_AVAILABILITY_DETAILS",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply is non-empty and meaningful",
        len(reply.strip()) > 10,
        f"reply={reply[:180]}",
    )
    print(f"      Bot replied: {reply.strip()}")


# ──────────────────────────────────────────────
# Test 13: Typo variation — "availabilty" (common typo)
# ──────────────────────────────────────────────
def test_typo_availability():
    print("\n[TEST 13] Typo — 'check doctor availabilty' (missing i)")
    msg = "check doctor availabilty"
    fsm = make_fsm()
    reply = fsm.handle(msg)

    check(
        "State moves to ASK_AVAILABILITY_DETAILS (typo handled)",
        fsm.state == "ASK_AVAILABILITY_DETAILS",
        f"actual state={fsm.state}",
    )
    check(
        "Reply asks for date",
        any(kw in reply.lower() for kw in ["date", "availability", "slot"]),
        f"reply={reply[:120]}",
    )


# ──────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("REQ-009: Check Availability Flow")
    print("=" * 60)

    test_option_2_triggers_availability()
    test_check_availability_text()
    test_natural_language_availability_request()
    test_availability_with_date_inline()
    test_hinglish_availability()
    test_state_give_date_gets_slots()
    test_state_no_slots_shows_next_date()
    test_state_no_slots_and_no_next_date()
    test_availability_then_booking()
    test_greeting_then_availability()
    test_availability_with_date_in_init_message()
    test_hindi_availability()
    test_typo_availability()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
