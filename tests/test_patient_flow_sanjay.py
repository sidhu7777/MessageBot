"""
FSM Integration Tests – Dr. Sanjay Vinayak (doctor_id=1, admin_id=1)
=====================================================================
Tests new-patient and known-patient booking flows using the real Aiven
MySQL database and the AppointmentFSM directly.

Scenarios covered:
  1. New patient – Telegram – full forward flow
  2. New patient – press 0 at ASK_CLINIC → should go back to ASK_PHONE
  3. Known patient – WhatsApp – name/phone auto-hydrated from DB, skipped
  4. Known patient – press 0 at ASK_CLINIC → should go back to ASK_BOOKING_FOR
  5. Dynamic clinic / date / time lists come from real DB (not hardcoded)

Run:
    python tests/test_patient_flow_sanjay.py
"""

import logging
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
    pytest.skip("Skipping live DB patient-flow tests (set RUN_LIVE_DB_TESTS=1 to enable).", allow_module_level=True)

logging.basicConfig(level=logging.WARNING)

# ── Doctor fixture ─────────────────────────────────────────────────────────────
DOCTOR_ID = 1   # Dr. Sanjay Vinayak
ADMIN_ID  = 1

# Known patient in DB for doctor_id=1 (patients.phone = '9999990000', full_name='Aashi')
KNOWN_PATIENT_PHONE = "9999990000"
KNOWN_PATIENT_NAME  = "Aashi"

# A Telegram chat ID that has a known_patient_name pre-set but NO existing booking in DB
# (simulates a known WhatsApp patient whose telegram_chat_id isn't registered yet)
KNOWN_TELEGRAM_CHAT_ID = "9999000088"
KNOWN_TELEGRAM_NAME    = "TestKnown"

# A phone / Telegram chat ID that does NOT exist in DB
UNKNOWN_TELEGRAM_1 = "telegram:9999000001"
UNKNOWN_TELEGRAM_2 = "telegram:9999000002"
UNKNOWN_TELEGRAM_3 = "telegram:9999000003"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_repos():
    db_url = os.getenv("DATABASE_URL", "")
    assert db_url, "DATABASE_URL env var is not set"
    config = parse_mysql_url(db_url)
    return BookingRepository(config), SchedulingRepository(config)


def make_fsm(
    chat_phone_number: str,
    known_patient_name: str | None = None,
) -> AppointmentFSM:
    """Create an AppointmentFSM backed by the real DB.

    LLM polish is disabled so numeric inputs work without an Ollama server.
    The LLMClient is still instantiated (ollama provider) but won't be called
    because:
      - enable_llm_polish=False suppresses all optional polish calls
      - Every user input in these tests is an unambiguous numeric choice or a
        clear phrase that is handled by rule-based NLU
    """
    booking_repo, scheduling_repo = _make_repos()
    llm = LLMClient(model="qwen3:0.6b", timeout_seconds=30.0)
    return AppointmentFSM(
        llm_client=llm,
        enable_llm_polish=False,
        booking_repository=booking_repo,
        scheduling_repository=scheduling_repo,
        doctor_id=DOCTOR_ID,
        admin_id=ADMIN_ID,
        chat_phone_number=chat_phone_number,
        known_patient_name=known_patient_name,
    )


def step(fsm: AppointmentFSM, user_input: str, label: str = "") -> str:
    """Send one message to the FSM and print a compact trace line."""
    before = fsm.state
    response = fsm.handle(user_input)
    after = fsm.state
    tag = f"[{label}] " if label else ""
    sample = response.replace("\n", " │ ")[:100]
    print(f"  {tag}› {user_input!r:20s}  {before!r:22s} → {after!r:22s}  {sample!r}")
    return response


def _section(title: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")


# ── Test 1 – New patient on Telegram: full forward booking flow ────────────────

def test_new_patient_telegram_full_flow():
    _section("TEST 1: New patient – Telegram – full forward flow")
    fsm = make_fsm(UNKNOWN_TELEGRAM_1)

    assert fsm.state == "INIT", f"Expected INIT, got {fsm.state}"

    # /start → bot greets as new patient, state stays INIT
    r = step(fsm, "/start", "/start")
    assert fsm.state == "INIT", "State should stay INIT after /start"
    assert fsm.known_patient_name is None, "New Telegram patient should have no known name"
    assert "doctor" in r.lower() or "welcome" in r.lower() or "नमस्ते" in r or "hello" in r.lower(), \
        "Expected a welcome/greeting message after /start"

    # "1" → BOOK_APPOINTMENT → ASK_BOOKING_FOR
    r = step(fsm, "1", "book")
    assert fsm.state == "ASK_BOOKING_FOR", f"Expected ASK_BOOKING_FOR, got {fsm.state}"

    # "1" (for self) → no known name → ASK_NAME
    r = step(fsm, "1", "for self")
    assert fsm.state == "ASK_NAME", (
        f"New patient 'for self' should go to ASK_NAME (no known name), got {fsm.state}"
    )

    # Enter name
    r = step(fsm, "Ramesh Kumar", "name")
    assert fsm.state == "ASK_PHONE", f"Expected ASK_PHONE after name, got {fsm.state}"
    assert fsm.context.patient_name == "Ramesh Kumar", \
        f"Name not captured: {fsm.context.patient_name!r}"

    # Enter phone
    r = step(fsm, "9876543210", "phone")
    assert fsm.state == "ASK_CLINIC", f"Expected ASK_CLINIC after phone, got {fsm.state}"
    assert fsm.context.phone_number == "9876543210", \
        f"Phone not captured: {fsm.context.phone_number!r}"

    # Clinic → real options from DB
    assert len(fsm.clinic_options_cache) > 0, \
        "clinic_options_cache must be populated from real DB before ASK_CLINIC"
    print(f"\n  DB clinics: {[c['name'] for c in fsm.clinic_options_cache]}")
    r = step(fsm, "1", "clinic=1")

    if fsm.state == "ASK_DATE":
        # Date options may be empty if no schedule configured for today's week
        r = step(fsm, "1", "date=1")
    if fsm.state == "ASK_TIME":
        r = step(fsm, "1", "time=1")

    # CONFIRM state: send "2" (change details) to avoid writing to DB during test
    if fsm.state == "CONFIRM":
        r = step(fsm, "2", "change – skip DB write")
        print("  (Sent 2=change-details at CONFIRM to avoid writing to real DB)")

    print("  PASS ✓")


# ── Test 2 – New patient: 0 at ASK_CLINIC must go back to ASK_PHONE ───────────

def test_new_patient_goback_at_clinic_goes_to_phone():
    _section("TEST 2: New patient – 0 at ASK_CLINIC → ASK_PHONE")
    fsm = make_fsm(UNKNOWN_TELEGRAM_2)

    step(fsm, "/start",       "/start")
    step(fsm, "1",            "book")
    step(fsm, "1",            "for self")          # → ASK_NAME  (no known name)
    step(fsm, "Priya Singh",  "name")              # → ASK_PHONE
    step(fsm, "9111222333",   "phone")             # → ASK_CLINIC

    assert fsm.state == "ASK_CLINIC", f"Setup failed: expected ASK_CLINIC, got {fsm.state}"

    r = step(fsm, "0", "go-back at ASK_CLINIC")
    assert fsm.state == "ASK_PHONE", (
        f"FAIL: New patient go-back at ASK_CLINIC should reach ASK_PHONE, got {fsm.state!r}"
    )
    print("  PASS ✓")


# ── Test 3 – Known patient on WhatsApp: name+phone auto-skipped ───────────────

def test_known_patient_whatsapp_skips_name_and_phone():
    _section("TEST 3: Known patient (WhatsApp) – name+phone auto-skipped from DB")
    # chat_phone_number = real patient phone in DB; booking_repository will hydrate name
    fsm = make_fsm(KNOWN_PATIENT_PHONE)

    assert fsm.state == "INIT"
    assert fsm.known_patient_name is None, "known_patient_name should be None before first message"

    # "hello" → greeting → _welcome_greeting() → DB lookup → known_patient_name set
    r = step(fsm, "hello", "greeting")
    assert fsm.known_patient_name is not None, (
        f"Expected DB to hydrate known_patient_name for phone {KNOWN_PATIENT_PHONE!r} "
        f"but got None. Verify patient exists for doctor_id={DOCTOR_ID} in DB."
    )
    print(f"\n  Auto-hydrated patient: {fsm.known_patient_name!r}")

    # Booking intent
    r = step(fsm, "1", "book")
    assert fsm.state == "ASK_BOOKING_FOR", f"Expected ASK_BOOKING_FOR, got {fsm.state}"

    # "1" (for self) → known name → skip name + phone → ASK_CLINIC directly
    r = step(fsm, "1", "for self")
    assert fsm.state == "ASK_CLINIC", (
        f"FAIL: Known patient 'for self' should skip name+phone → ASK_CLINIC, got {fsm.state!r}"
    )
    assert fsm.context.patient_name == fsm.known_patient_name, \
        "context.patient_name should equal known_patient_name"
    print(f"  Correctly skipped name+phone → state={fsm.state!r}, "
          f"patient_name={fsm.context.patient_name!r}")
    print("  PASS ✓")


# ── Test 4 – Known patient: 0 at ASK_CLINIC must go back to ASK_BOOKING_FOR ──

def test_known_patient_goback_at_clinic_goes_to_booking_for():
    _section("TEST 4: Known patient – 0 at ASK_CLINIC → ASK_BOOKING_FOR")
    # Pre-set known_patient_name; skip /start because _welcome_greeting() would reset it
    # (Welcome greeting resets known_patient_name then re-hydrates from DB; since this
    # Telegram chat_id has no DB record the name would be lost.)
    # We send "1" directly as first message → BOOK_APPOINTMENT via rule-based routing.
    fsm = make_fsm(
        f"telegram:{KNOWN_TELEGRAM_CHAT_ID}",
        known_patient_name=KNOWN_TELEGRAM_NAME,
    )

    step(fsm, "1",  "book (skip /start to preserve known_patient_name)")
    assert fsm.state == "ASK_BOOKING_FOR", (
        f"Expected ASK_BOOKING_FOR after '1', got {fsm.state!r}"
    )

    r = step(fsm, "1", "for self (known)")
    assert fsm.state == "ASK_CLINIC", (
        f"Setup: Known patient 'for self' should reach ASK_CLINIC directly, got {fsm.state!r}"
    )
    assert fsm.context.patient_name == KNOWN_TELEGRAM_NAME, \
        f"context.patient_name should be {KNOWN_TELEGRAM_NAME!r}, got {fsm.context.patient_name!r}"

    r = step(fsm, "0", "go-back at ASK_CLINIC")
    assert fsm.state == "ASK_BOOKING_FOR", (
        f"FAIL: Known patient go-back at ASK_CLINIC should reach ASK_BOOKING_FOR, "
        f"got {fsm.state!r}. (Fix: _handle_go_back ASK_CLINIC case)"
    )
    print("  PASS ✓")


# ── Test 5 – Dynamic DB values: clinic / date / time are NOT hardcoded ─────────

def test_dynamic_db_options_not_hardcoded():
    _section("TEST 5: Dynamic options – clinic / date / time from real DB")
    fsm = make_fsm(UNKNOWN_TELEGRAM_3)

    step(fsm, "/start",        "/start")
    step(fsm, "1",             "book")
    step(fsm, "1",             "for self")
    step(fsm, "Deepak Sharma", "name")
    step(fsm, "9800000001",    "phone")

    assert fsm.state == "ASK_CLINIC", f"Expected ASK_CLINIC, got {fsm.state}"

    # Clinic options come from DB
    clinic_names = [c["name"] for c in fsm.clinic_options_cache]
    assert len(clinic_names) > 0, \
        "clinic_options_cache must be non-empty – real DB has clinics for doctor_id=1"
    print(f"\n  Real clinics (doctor_id=1): {clinic_names}")

    # These should match true DB names, NOT any hardcoded value
    assert any(n.strip() for n in clinic_names), "Clinic names should be non-blank strings"
    # Verify at least one well-known clinic name exists (dynamic check)
    known_clinics = {"city care clinic", "health plus clinic", "aditya"}
    assert any(n.lower().strip() in known_clinics for n in clinic_names), (
        f"None of the expected clinics found in {clinic_names}. DB may have changed."
    )

    step(fsm, "1", "clinic=1")

    if fsm.state == "ASK_DATE":
        print(f"  Real dates available: {fsm.date_options_cache}")
        # date_options may be empty if doctor has no upcoming schedule;
        # the important thing is that we reached ASK_DATE (not a hardcoded branch)
        if fsm.date_options_cache:
            step(fsm, "1", "date=1")

    if fsm.state == "ASK_TIME":
        print(f"  Real time-windows: {fsm.time_window_labels_cache or fsm.time_options_cache}")
        if fsm.time_options_cache or fsm.time_slot_options_cache:
            step(fsm, "1", "time=1")

    print("  PASS ✓")


# ── Test 6 – "For another person" path does NOT skip name/phone ────────────────

def test_booking_for_other_still_asks_name_and_phone():
    _section("TEST 6: Booking for 'another person' – always asks name + phone")
    # Even a known patient booking for someone else must ask name+phone
    fsm = make_fsm(UNKNOWN_TELEGRAM_1, known_patient_name="FakeKnown")

    step(fsm, "/start", "/start")
    step(fsm, "1",      "book")

    r = step(fsm, "2", "for another person")
    assert fsm.state == "ASK_NAME", (
        f"'For other person' path should always go to ASK_NAME, got {fsm.state!r}"
    )
    assert fsm.booking_for_self is False, "booking_for_self should be False"
    print("  PASS ✓")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_new_patient_telegram_full_flow,
    test_new_patient_goback_at_clinic_goes_to_phone,
    test_known_patient_whatsapp_skips_name_and_phone,
    test_known_patient_goback_at_clinic_goes_to_booking_for,
    test_dynamic_db_options_not_hardcoded,
    test_booking_for_other_still_asks_name_and_phone,
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
            print(f"  ✗ ASSERTION FAILED: {exc}")
        except Exception as exc:
            failed += 1
            errors.append((fn.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ✗ ERROR: {type(exc).__name__}: {exc}")

    print(f"\n{'─' * 70}")
    print(f"  Results: {passed} passed, {failed} failed  (total {len(TESTS)})")
    if errors:
        print("\n  Failed tests:")
        for name, msg in errors:
            print(f"    ✗ {name}: {msg}")
    else:
        print("  All tests passed ✓")
    print(f"{'─' * 70}\n")
    sys.exit(0 if failed == 0 else 1)
