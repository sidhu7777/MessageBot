"""
Test: Telegram Known Patient via chat_id
=========================================
Verifies the fix: _hydrate_known_patient_name now looks up patients.telegram_chat_id
for Telegram patients, instead of silently skipping as before.

DB fixture used:
  patients table → patient_id=5, full_name='Ghrdftufg',
                   telegram_chat_id='8299824956', doctor_id=1, admin_id=1

Scenarios:
  1. Raw DB query – find_patient_name_by_chat_user_id returns correct name
  2. FSM hydration – _hydrate_known_patient_name sets known_patient_name via chat_id
  3. Booking flow – once hydrated, "for self" skips ASK_NAME + ASK_PHONE → ASK_CLINIC
  4. Go-back – pressing 0 at ASK_CLINIC goes to ASK_BOOKING_FOR (not ASK_PHONE)

Run:
    python tests/test_telegram_known_patient.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.db.connection import parse_mysql_url
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository

# ── Fixtures ───────────────────────────────────────────────────────────────────
DOCTOR_ID  = 1
ADMIN_ID   = 1

# Patient in DB with telegram_chat_id set
TELEGRAM_CHAT_ID    = "8299824956"       # patients.telegram_chat_id
EXPECTED_NAME       = "Ghrdftufg"        # patients.full_name
FSM_CHAT_PHONE      = f"telegram:{TELEGRAM_CHAT_ID}"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_repos():
    config = parse_mysql_url(os.getenv("DATABASE_URL", ""))
    return BookingRepository(config), SchedulingRepository(config)


def _make_fsm(chat_phone: str) -> AppointmentFSM:
    booking_repo, scheduling_repo = _make_repos()
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
    sample = response.replace("\n", " │ ")[:110]
    print(f"  {tag}› {user_input!r:20s}  {before!r:22s} → {after!r:22s}  {sample!r}")
    return response


def _section(title: str) -> None:
    print(f"\n{'═' * 72}")
    print(f"  {title}")
    print(f"{'═' * 72}")


# ── Test 1 – Direct DB query ───────────────────────────────────────────────────

def test_repo_find_patient_name_by_chat_user_id():
    _section("TEST 1: BookingRepository.find_patient_name_by_chat_user_id (raw DB query)")
    booking_repo, _ = _make_repos()

    # With "telegram:" prefix
    name = booking_repo.find_patient_name_by_chat_user_id(
        chat_user_id=FSM_CHAT_PHONE,
        admin_id=ADMIN_ID,
        doctor_id=DOCTOR_ID,
    )
    print(f"\n  find_patient_name_by_chat_user_id({FSM_CHAT_PHONE!r}) → {name!r}")
    assert name == EXPECTED_NAME, (
        f"FAIL: Expected {EXPECTED_NAME!r}, got {name!r}. "
        f"Verify patients.telegram_chat_id='{TELEGRAM_CHAT_ID}' exists for doctor_id={DOCTOR_ID}"
    )

    # Without "telegram:" prefix (raw numeric string — should also work)
    name2 = booking_repo.find_patient_name_by_chat_user_id(
        chat_user_id=TELEGRAM_CHAT_ID,
        admin_id=ADMIN_ID,
        doctor_id=DOCTOR_ID,
    )
    print(f"  find_patient_name_by_chat_user_id({TELEGRAM_CHAT_ID!r}) → {name2!r}")
    assert name2 == EXPECTED_NAME, (
        f"FAIL: Lookup without 'telegram:' prefix failed — got {name2!r}"
    )

    print("  PASS ✓")


# ── Test 2 – FSM _hydrate_known_patient_name via chat_id ──────────────────────

def test_fsm_hydrate_known_patient_name_via_telegram_chat_id():
    _section("TEST 2: FSM _hydrate_known_patient_name sets name via telegram_chat_id")
    fsm = _make_fsm(FSM_CHAT_PHONE)

    assert fsm.known_patient_name is None, "known_patient_name should start as None"
    assert fsm._is_telegram_channel(), f"Expected Telegram channel for {FSM_CHAT_PHONE!r}"

    # Call the private hydration method directly
    fsm._hydrate_known_patient_name()

    print(f"\n  known_patient_name after hydration: {fsm.known_patient_name!r}")
    assert fsm.known_patient_name == EXPECTED_NAME, (
        f"FAIL: Expected known_patient_name={EXPECTED_NAME!r}, got {fsm.known_patient_name!r}\n"
        f"       The fix to _hydrate_known_patient_name is not working correctly."
    )
    print("  PASS ✓")


# ── Test 3 – Booking flow: known Telegram patient skips ASK_NAME + ASK_PHONE ─

def test_telegram_known_patient_skips_name_and_phone():
    _section("TEST 3: Known Telegram patient – 'for self' skips ASK_NAME + ASK_PHONE → ASK_CLINIC")
    fsm = _make_fsm(FSM_CHAT_PHONE)

    # Manually hydrate then force state to ASK_BOOKING_FOR
    # (bypasses existing-booking check so we can test the name-skip logic cleanly)
    fsm._hydrate_known_patient_name()
    assert fsm.known_patient_name == EXPECTED_NAME, (
        f"Hydration prerequisite failed: got {fsm.known_patient_name!r}"
    )
    fsm.state = "ASK_BOOKING_FOR"
    print(f"\n  known_patient_name = {fsm.known_patient_name!r}, state forced to ASK_BOOKING_FOR")

    r = step(fsm, "1", "for self")
    assert fsm.state == "ASK_CLINIC", (
        f"FAIL: Known Telegram patient 'for self' should go directly to ASK_CLINIC, "
        f"got {fsm.state!r}. "
        f"Name={fsm.context.patient_name!r}, known={fsm.known_patient_name!r}"
    )
    assert fsm.context.patient_name == EXPECTED_NAME, (
        f"FAIL: context.patient_name should be auto-filled as {EXPECTED_NAME!r}, "
        f"got {fsm.context.patient_name!r}"
    )
    print(f"  Correctly skipped name+phone → state={fsm.state!r}, "
          f"patient_name={fsm.context.patient_name!r}")
    print("  PASS ✓")


# ── Test 4 – Go-back at ASK_CLINIC for known Telegram patient ─────────────────

def test_telegram_known_patient_goback_at_clinic_goes_to_booking_for():
    _section("TEST 4: Known Telegram patient – 0 at ASK_CLINIC → ASK_BOOKING_FOR (not ASK_PHONE)")
    fsm = _make_fsm(FSM_CHAT_PHONE)

    fsm._hydrate_known_patient_name()
    fsm.state = "ASK_BOOKING_FOR"
    step(fsm, "1", "for self (to reach ASK_CLINIC)")
    assert fsm.state == "ASK_CLINIC", f"Setup failed: expected ASK_CLINIC, got {fsm.state!r}"

    r = step(fsm, "0", "go-back at ASK_CLINIC")
    assert fsm.state == "ASK_BOOKING_FOR", (
        f"FAIL: Known patient go-back at ASK_CLINIC should reach ASK_BOOKING_FOR, "
        f"got {fsm.state!r}"
    )
    print("  PASS ✓")


# ── Test 5 – Confirm WhatsApp path still works (no regression) ────────────────

def test_whatsapp_phone_hydration_not_broken():
    _section("TEST 5: WhatsApp phone hydration still works (regression check)")
    # patient_id=6, full_name='Aashi', phone='9999990000', doctor_id=1
    WHATSAPP_PHONE   = "9999990000"
    EXPECTED_WA_NAME = "Aashi"

    booking_repo, scheduling_repo = _make_repos()
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", timeout_seconds=30.0),
        enable_llm_polish=False,
        booking_repository=booking_repo,
        scheduling_repository=scheduling_repo,
        doctor_id=DOCTOR_ID,
        admin_id=ADMIN_ID,
        chat_phone_number=WHATSAPP_PHONE,
    )
    assert not fsm._is_telegram_channel(), "Should NOT be Telegram for a plain phone number"

    fsm._hydrate_known_patient_name()
    print(f"\n  WhatsApp phone {WHATSAPP_PHONE!r} → known_patient_name={fsm.known_patient_name!r}")
    assert fsm.known_patient_name == EXPECTED_WA_NAME, (
        f"FAIL: Expected {EXPECTED_WA_NAME!r}, got {fsm.known_patient_name!r}"
    )
    print("  PASS ✓")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_repo_find_patient_name_by_chat_user_id,
    test_fsm_hydrate_known_patient_name_via_telegram_chat_id,
    test_telegram_known_patient_skips_name_and_phone,
    test_telegram_known_patient_goback_at_clinic_goes_to_booking_for,
    test_whatsapp_phone_hydration_not_broken,
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

    print(f"\n{'─' * 72}")
    print(f"  Results: {passed} passed, {failed} failed  (total {len(TESTS)})")
    if errors:
        print("\n  Failed tests:")
        for name, msg in errors:
            print(f"    FAIL {name}: {msg}")
    else:
        print("  All tests passed")
    print(f"{'─' * 72}\n")
    sys.exit(0 if failed == 0 else 1)
