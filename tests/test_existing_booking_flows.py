"""
Test: Existing Booking Flow – 4 Scenarios
==========================================
When a patient already has an active booking the bot shows:

    You already have a booked appointment:
    Booking Number: X | Clinic: Y | Date: D | Time: T

    1. Keep it
    2. Cancel
    3. Reschedule
    4. Book for another person

This file tests each of those 4 options independently.

DB fixture used (doctor_id=1, admin_id=1):
  Patient: Anant | telegram_chat_id='6935976617' | appointment_id=16
           Clinic: Aditya | Date: 2026-03-02 | status=BOOKED
  (Single active booking → no multi-pick flow)

Run:
    python tests/test_existing_booking_flows.py
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.db.connection import parse_mysql_url
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository

if os.getenv("RUN_LIVE_DB_TESTS", "0") != "1":
    pytest.skip("Skipping live DB existing-booking flow tests (set RUN_LIVE_DB_TESTS=1 to enable).", allow_module_level=True)

# ── Fixtures ───────────────────────────────────────────────────────────────────
DOCTOR_ID = 1
ADMIN_ID  = 1

# Anant – 1 active BOOKED appointment (apt_id=16, clinic=Aditya)
ANANT_TELEGRAM_CHAT_ID = "6935976617"
ANANT_CHAT_PHONE       = f"telegram:{ANANT_TELEGRAM_CHAT_ID}"


# ── MockBookingRepo – wraps real repo, overrides cancel to avoid DB write ──────

class _MockCancelRepo(BookingRepository):
    """Real BookingRepository except cancel_appointment always returns True
    without touching the DB (so tests are safe to re-run)."""

    def __init__(self, config):
        super().__init__(config)
        self.cancelled_ids: list[int] = []

    def cancel_appointment(self, appointment_id, admin_id=None, cancelled_by="PATIENT"):
        # Record the id that would have been cancelled but don't hit DB
        self.cancelled_ids.append(appointment_id)
        return True


# ── Helpers ────────────────────────────────────────────────────────────────────

def _config():
    return parse_mysql_url(os.getenv("DATABASE_URL", ""))


def _make_fsm(chat_phone: str, mock_cancel: bool = False) -> AppointmentFSM:
    config = _config()
    booking_repo = _MockCancelRepo(config) if mock_cancel else BookingRepository(config)
    scheduling_repo = SchedulingRepository(config)
    return AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", timeout_seconds=30.0),
        enable_llm_polish=False,
        booking_repository=booking_repo,
        scheduling_repository=scheduling_repo,
        doctor_id=DOCTOR_ID,
        admin_id=ADMIN_ID,
        chat_phone_number=chat_phone,
    )


def step(fsm: AppointmentFSM, user_input: str, label: str = "") -> str:
    before = fsm.state
    response = fsm.handle(user_input)
    after = fsm.state
    tag = f"[{label}] " if label else ""
    sample = response.replace("\n", " | ")[:120]
    print(f"  {tag}> {user_input!r:15s}  {before!r:28s} -> {after!r:28s}  {sample!r}")
    return response


def _reach_existing_booking_prompt(fsm: AppointmentFSM) -> str:
    """Send /start and return the bot response. Must land on ASK_EXISTING_BOOKING_ACTION."""
    r = step(fsm, "/start", "/start")
    assert fsm.state == "ASK_EXISTING_BOOKING_ACTION", (
        f"Expected ASK_EXISTING_BOOKING_ACTION after /start for patient with active booking, "
        f"got {fsm.state!r}. Verify appointment for telegram_chat_id={ANANT_TELEGRAM_CHAT_ID!r} "
        f"still exists and is BOOKED/PENDING/CONFIRMED."
    )
    return r


def _section(title: str) -> None:
    print(f"\n{'=' * 74}")
    print(f"  {title}")
    print(f"{'=' * 74}")


# ── Test 1 – Option 1: Keep existing booking ───────────────────────────────────

def test_existing_booking_option1_keep():
    _section("TEST 1: Existing booking -> Option 1 (Keep) -> appointment stays, state=COMPLETED")
    fsm = _make_fsm(ANANT_CHAT_PHONE)

    r = _reach_existing_booking_prompt(fsm)
    print(f"\n  Bot prompt:\n    {r[:300]}\n")

    # Verify the bot mentions all 4 options in the prompt
    assert "1" in r, "Option 1 (Keep) should be in the prompt"
    assert "2" in r, "Option 2 (Cancel) should be in the prompt"
    assert "3" in r, "Option 3 (Reschedule) should be in the prompt"
    assert "4" in r, "Option 4 (Another person) should be in the prompt"

    # Patient chooses 1 = Keep
    r2 = step(fsm, "1", "keep")
    assert fsm.state == "COMPLETED", (
        f"After option 1 (keep), state should be COMPLETED, got {fsm.state!r}"
    )
    assert any(word in r2.lower() for word in ["kept", "keep", "existing", "same", "theek"]), (
        f"Response should confirm appointment is kept, got: {r2!r}"
    )
    print(f"\n  Bot confirmation: {r2!r}")
    print("  PASS")


# ── Test 2 – Option 2: Cancel existing booking ────────────────────────────────

def test_existing_booking_option2_cancel():
    _section("TEST 2: Existing booking -> Option 2 (Cancel) -> appointment cancelled, state=COMPLETED")
    # Use mock so we don't actually delete the DB record
    fsm = _make_fsm(ANANT_CHAT_PHONE, mock_cancel=True)
    mock_repo: _MockCancelRepo = fsm.booking_repository  # type: ignore

    _reach_existing_booking_prompt(fsm)

    # Capture which appointment_id was found
    apt_id_found = fsm.existing_appointment_id
    print(f"\n  Existing appointment_id found by FSM: {apt_id_found}")
    assert apt_id_found is not None, "FSM must have captured existing_appointment_id"

    # Patient chooses 2 = Cancel
    r = step(fsm, "2", "cancel")
    assert fsm.state == "COMPLETED", (
        f"After option 2 (cancel), state should be COMPLETED, got {fsm.state!r}"
    )
    assert any(word in r.lower() for word in ["cancel", "cancelled", "done"]), (
        f"Response should confirm cancellation, got: {r!r}"
    )
    assert apt_id_found in mock_repo.cancelled_ids, (
        f"cancel_appointment() must be called with appointment_id={apt_id_found}, "
        f"called with: {mock_repo.cancelled_ids}"
    )
    print(f"\n  Cancelled appointment_id: {mock_repo.cancelled_ids}")
    print(f"  Bot confirmation: {r!r}")
    print("  PASS")


# ── Test 3 – Option 3: Reschedule existing booking ────────────────────────────

def test_existing_booking_option3_reschedule():
    _section("TEST 3: Existing booking -> Option 3 (Reschedule) -> shows clinics to pick new slot")
    fsm = _make_fsm(ANANT_CHAT_PHONE)

    _reach_existing_booking_prompt(fsm)

    old_clinic = fsm.existing_booking_clinic_name
    old_date   = fsm.existing_booking_old_date
    print(f"\n  Existing booking: clinic={old_clinic!r}, date={old_date!r}")
    assert old_clinic, "existing_booking_clinic_name must be populated"

    # Patient chooses 3 = Reschedule
    r = step(fsm, "3", "reschedule")
    assert fsm.state == "ASK_CLINIC", (
        f"After option 3 (reschedule), state should be ASK_CLINIC (to pick new clinic), "
        f"got {fsm.state!r}"
    )
    assert fsm.in_reschedule_flow is True, (
        "in_reschedule_flow flag must be True during reschedule"
    )
    # Old date/time must be cleared so fresh selection is forced
    assert fsm.context.appointment_date is None, (
        f"appointment_date must be cleared for rescheduling, got {fsm.context.appointment_date!r}"
    )
    assert fsm.context.appointment_time is None, (
        f"appointment_time must be cleared for rescheduling, got {fsm.context.appointment_time!r}"
    )
    # Response should mention previous clinic and show new clinic options
    assert old_clinic.lower() in r.lower() or "clinic" in r.lower(), (
        f"Response should show clinic options, got: {r!r}"
    )
    print(f"\n  Bot response (first 200 chars): {r[:200]!r}")
    print(f"  in_reschedule_flow={fsm.in_reschedule_flow}")

    # Verify real clinic options are loaded from DB
    assert len(fsm.clinic_options_cache) > 0, "Clinic options must be loaded from DB"
    print(f"  Available clinics: {[c['name'] for c in fsm.clinic_options_cache]}")

    # Continue: pick clinic 1, then check it moves to ASK_DATE
    r2 = step(fsm, "1", "pick clinic")
    assert fsm.state == "ASK_DATE", (
        f"After clinic selection during reschedule, state should be ASK_DATE, "
        f"got {fsm.state!r}"
    )
    print(f"  Moving to date selection -> state={fsm.state!r}")
    print("  PASS")


# ── Test 4 – Option 4: Book for another person ────────────────────────────────

def test_existing_booking_option4_book_for_another():
    _section("TEST 4: Existing booking -> Option 4 (Another person) -> fresh ASK_NAME for new patient")
    fsm = _make_fsm(ANANT_CHAT_PHONE)

    _reach_existing_booking_prompt(fsm)

    # Patient chooses 4 = Book for another person
    r = step(fsm, "4", "another person")

    # Anant has 1 active booking (< 2 limit) so allowed to book for another
    assert fsm.state == "ASK_NAME", (
        f"After option 4 (another person), state should be ASK_NAME, got {fsm.state!r}"
    )
    assert fsm.booking_for_self is False, (
        "booking_for_self must be False when booking for another person"
    )
    # Previous booking context must be cleared
    assert fsm.context.patient_name is None, (
        f"patient_name context must be cleared for new booking, got {fsm.context.patient_name!r}"
    )
    assert fsm.context.phone_number is None, (
        f"phone_number context must be cleared for new booking, got {fsm.context.phone_number!r}"
    )
    print(f"\n  Bot asks for name: {r!r}")
    print(f"  booking_for_self={fsm.booking_for_self}, patient_name={fsm.context.patient_name!r}")

    # Continue: supply a name → should move to ASK_PHONE
    r2 = step(fsm, "Ravi Sharma", "new patient name")
    assert fsm.state == "ASK_PHONE", (
        f"After entering name for another person, state should be ASK_PHONE, got {fsm.state!r}"
    )
    assert fsm.context.patient_name == "Ravi Sharma", (
        f"context.patient_name should be 'Ravi Sharma', got {fsm.context.patient_name!r}"
    )
    print(f"  Name captured: {fsm.context.patient_name!r} -> state={fsm.state!r}")
    print("  PASS")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_existing_booking_option1_keep,
    test_existing_booking_option2_cancel,
    test_existing_booking_option3_reschedule,
    test_existing_booking_option4_book_for_another,
]

if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed += 1
            errors.append((fn.__name__, str(exc)))
            print(f"  ASSERTION FAILED: {exc}")
        except Exception as exc:
            failed += 1
            errors.append((fn.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR: {type(exc).__name__}: {exc}")

    print(f"\n{'─' * 74}")
    print(f"  Results: {passed} passed, {failed} failed  (total {len(TESTS)})")
    if errors:
        print("\n  Failed tests:")
        for name, msg in errors:
            print(f"    FAIL {name}: {msg}")
    else:
        print("  All tests passed")
    print(f"{'─' * 74}\n")
    sys.exit(0 if failed == 0 else 1)
