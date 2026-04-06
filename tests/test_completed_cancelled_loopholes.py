"""
TESTS: COMPLETED and CANCELLED state loopholes
================================================
Covers exactly 3 fixes:

  Fix 1 — COMPLETED + greeting (no active booking) → welcome + menu
           (was: "This appointment flow is complete. Send 'book appointment'...")

  Fix 2 — COMPLETED + is_booking_intent → no double DB call
           (was: _existing_booking_entry_response() called twice in same turn)

  Fix 3 — CANCELLED + greeting (no active booking) → welcome + menu
           (was: "Process is ended. Send 'book appointment'...")

Each fix also has a counter-test that proves the UNCHANGED behaviour
(when patient HAS an active booking, the existing-booking menu still appears).

Run:
  $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_completed_cancelled_loopholes.py
"""

import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.conversation_repository import SessionSnapshot
from src.repositories.scheduling_repository import ClinicOption
from src.session_store import SessionManager
from src.llm.client import LLMClient

PASS = 0
FAIL = 0
_SECTION: str = ""


def section(title: str) -> None:
    global _SECTION
    _SECTION = title
    print(f"\n{'─'*65}")
    print(f"  {title}")
    print(f"{'─'*65}")


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    prefix = f"[{_SECTION}] " if _SECTION else ""
    if ok:
        PASS += 1
        print(f"  [PASS] {prefix}{label}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {prefix}{label}"
        if detail:
            msg += f"\n         ↳ {detail}"
        print(msg)


# ─────────────────────────────────────────────────────────────────────────────
# MockConversationRepo — same pattern as test_behaviour_contract.py
# ─────────────────────────────────────────────────────────────────────────────

class MockConversationRepo:
    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}

    def save_session(self, *, user_id, state, context, response_language,
                     language_locked, language_turn_count, init_unclear_count,
                     in_edit_flow, doctor_id, admin_id, fsm_extra_json=None) -> None:
        self._rows[user_id] = {
            "user_id": user_id, "state": state,
            "context_json": json.dumps(context, ensure_ascii=False),
            "response_language": response_language,
            "language_locked": language_locked,
            "language_turn_count": language_turn_count,
            "init_unclear_count": init_unclear_count,
            "in_edit_flow": in_edit_flow,
            "doctor_id": doctor_id, "admin_id": admin_id,
            "fsm_extra_json": fsm_extra_json,
            "updated_at": datetime.utcnow(),
        }

    def load_session(self, user_id: str, ttl_minutes: int) -> Optional[SessionSnapshot]:
        row = self._rows.get(user_id)
        if not row:
            return None
        return SessionSnapshot(
            user_id=row["user_id"], state=row["state"],
            context_json=row["context_json"],
            response_language=row["response_language"],
            language_locked=bool(row["language_locked"]),
            language_turn_count=int(row["language_turn_count"] or 0),
            init_unclear_count=int(row["init_unclear_count"] or 0),
            in_edit_flow=bool(row["in_edit_flow"]),
            doctor_id=row["doctor_id"], admin_id=row["admin_id"],
            updated_at=row["updated_at"],
            fsm_extra_json=row.get("fsm_extra_json"),
        )

    def ensure_schema(self) -> None:
        pass

    def seed_state(self, user_id: str, state: str,
                   doctor_id: int = 1, admin_id: int = 1) -> None:
        """Directly write a session row with the given state — simulates what
        the DB would hold after the bot previously saved this FSM state."""
        self._rows[user_id] = {
            "user_id": user_id, "state": state,
            "context_json": "{}",
            "response_language": "en",
            "language_locked": False,
            "language_turn_count": 0,
            "init_unclear_count": 0,
            "in_edit_flow": False,
            "doctor_id": doctor_id, "admin_id": admin_id,
            "fsm_extra_json": json.dumps({
                "known_patient_name": None,
                "booking_for_self": None,
                "selected_time_period": None,
                "time_slot_options_cache": [],
                "time_window_labels_cache": [],
                "in_reschedule_flow": False,
                "pending_existing_action": None,
                "existing_appointment_id": None,
                "existing_booking_clinic_id": None,
                "existing_booking_clinic_name": None,
                "existing_booking_doctor_id": None,
                "existing_booking_old_date": None,
                "existing_booking_old_time": None,
                "active_booking_options_cache": [],
            }),
            "updated_at": datetime.utcnow(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Repo / session helpers
# ─────────────────────────────────────────────────────────────────────────────

_ACTIVE_BOOKING_ROW = {
    "appointment_id": 42,
    "clinic_id": 1,
    "clinic_name": "City Clinic",
    "doctor_id": 1,
    "slot_date": "2026-03-10",
    "slot_time": "10:00",
    "booking_number": 3,
}


def _make_booking_repo(has_active_booking: bool = False,
                       known_name: Optional[str] = None) -> MagicMock:
    repo = MagicMock()
    active = [_ACTIVE_BOOKING_ROW] if has_active_booking else []
    repo.list_active_appointments_by_chat_user_id.return_value = active
    repo.list_active_appointments_by_phone_number.return_value = active
    repo.find_patient_name_by_chat_user_id.return_value = known_name
    repo.find_patient_name_by_phone_number.return_value = known_name
    repo.find_patient_phone_by_chat_user_id.return_value = "9876543210"
    repo.get_doctor_display_name.return_value = "Dr. Sanjay"
    repo.default_admin_id.return_value = 1
    return repo


def _make_scheduling_repo() -> MagicMock:
    sch = MagicMock()
    sch.default_doctor_id.return_value = 1
    sch.default_doctor_id_by_username.return_value = 1
    sch.default_doctor_id_by_phone.return_value = 1
    sch.doctor_accept_days.return_value = 2
    sch.list_available_dates.return_value = ["2026-03-10"]
    sch.list_available_times.return_value = ["10:00", "10:20", "11:00"]
    sch.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Clinic", location="Noida", today_slots=5)
    ]
    return sch


def _make_llm(intent: str = "GREETING") -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    llm.generate.return_value = intent
    return llm


def _make_session_manager(br, sch, cr, llm=None) -> SessionManager:
    return SessionManager(
        llm_client=llm or _make_llm(),
        enable_llm_polish=False,
        booking_repository=br,
        scheduling_repository=sch,
        conversation_repository=cr,
        redis_client=None,
    )


def turn(sm: SessionManager, uid: str, text: str) -> str:
    fsm = sm.get_or_create(uid)
    reply = fsm.handle(text)
    sm.save(uid, fsm)
    return reply


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — COMPLETED + greeting + NO active booking → welcome + menu
# ─────────────────────────────────────────────────────────────────────────────

def test_1_completed_greeting_no_active_booking() -> None:
    section("TEST 1: COMPLETED + 'Hello' + no active booking → welcome menu shown")

    cr = MockConversationRepo()
    uid = "telegram:test_completed_hello"
    cr.seed_state(uid, "COMPLETED")

    br = _make_booking_repo(has_active_booking=False, known_name=None)
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("GREETING"))

    reply = turn(sm, uid, "Hello")

    check(
        "Reply does NOT contain completed_hint text",
        "appointment flow is complete" not in reply.lower() and
        "send 'book appointment'" not in reply.lower(),
        f"bot said: {reply!r}",
    )
    check(
        "Reply contains the 1/2 menu (clarify_intent)",
        "1." in reply and "2." in reply,
        f"bot said: {reply!r}",
    )
    # After this turn the FSM must be back in INIT (reset to fresh)
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state reset to INIT after greeting from COMPLETED",
        loaded is not None and loaded.state == "INIT",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — COMPLETED + greeting + HAS active booking → existing-booking menu
#          (unchanged behaviour — must NOT be broken by fix)
# ─────────────────────────────────────────────────────────────────────────────

def test_2_completed_greeting_has_active_booking_unchanged() -> None:
    section("TEST 2: COMPLETED + 'Hello' + HAS active booking → existing-booking menu (unchanged)")

    cr = MockConversationRepo()
    uid = "telegram:test_completed_active"
    cr.seed_state(uid, "COMPLETED")

    br = _make_booking_repo(has_active_booking=True, known_name="Vineeth Raja")
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("GREETING"))

    reply = turn(sm, uid, "Hello")

    check(
        "Reply shows existing-booking menu",
        "You already have a booked appointment" in reply or
        "already have" in reply.lower() or
        "existing" in reply.lower() or
        "Booking Number" in reply,
        f"bot said: {reply!r}",
    )
    check(
        "Reply does NOT show generic clarify_intent menu",
        "Book appointment\n2." not in reply,
        f"bot said: {reply!r}",
    )
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state is ASK_EXISTING_BOOKING_ACTION",
        loaded is not None and loaded.state == "ASK_EXISTING_BOOKING_ACTION",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — COMPLETED + "book appointment" + no active booking → ASK_BOOKING_FOR
#          AND _existing_booking_entry_response called only once (no double DB call)
# ─────────────────────────────────────────────────────────────────────────────

def test_3_completed_book_intent_no_double_db_call() -> None:
    section("TEST 3: COMPLETED + 'book appointment' → ASK_BOOKING_FOR, single DB call")

    cr = MockConversationRepo()
    uid = "telegram:test_completed_book"
    cr.seed_state(uid, "COMPLETED")

    br = _make_booking_repo(has_active_booking=False, known_name=None)
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("BOOK_APPOINTMENT"))

    reply = turn(sm, uid, "book appointment")

    check(
        "Reply contains ASK_BOOKING_FOR prompt",
        "Who is this appointment for" in reply or
        "1. Self" in reply or
        "Self" in reply,
        f"bot said: {reply!r}",
    )

    # Verify single DB call — list_active_appointments_by_chat_user_id called exactly once
    call_count = br.list_active_appointments_by_chat_user_id.call_count
    check(
        "list_active_appointments called exactly once (no double DB call)",
        call_count == 1,
        f"was called {call_count} times",
    )

    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state is ASK_BOOKING_FOR",
        loaded is not None and loaded.state == "ASK_BOOKING_FOR",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — COMPLETED + "book appointment" + HAS active booking → existing-booking menu
#          (unchanged behaviour)
# ─────────────────────────────────────────────────────────────────────────────

def test_4_completed_book_intent_has_active_booking_unchanged() -> None:
    section("TEST 4: COMPLETED + 'book appointment' + HAS active booking → existing-booking menu (unchanged)")

    cr = MockConversationRepo()
    uid = "telegram:test_completed_book_active"
    cr.seed_state(uid, "COMPLETED")

    br = _make_booking_repo(has_active_booking=True, known_name="Vineeth Raja")
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("BOOK_APPOINTMENT"))

    reply = turn(sm, uid, "book appointment")

    check(
        "Reply shows existing-booking menu even for book intent",
        "already have" in reply.lower() or
        "Booking Number" in reply or
        "existing" in reply.lower(),
        f"bot said: {reply!r}",
    )
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state is ASK_EXISTING_BOOKING_ACTION",
        loaded is not None and loaded.state == "ASK_EXISTING_BOOKING_ACTION",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — CANCELLED + greeting + NO active booking → welcome + menu
# ─────────────────────────────────────────────────────────────────────────────

def test_5_cancelled_greeting_no_active_booking() -> None:
    section("TEST 5: CANCELLED + 'Hello' + no active booking → welcome menu shown")

    cr = MockConversationRepo()
    uid = "telegram:test_cancelled_hello"
    cr.seed_state(uid, "CANCELLED")

    br = _make_booking_repo(has_active_booking=False, known_name=None)
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("GREETING"))

    reply = turn(sm, uid, "Hello")

    check(
        "Reply does NOT contain cancelled_hint text",
        "process is ended" not in reply.lower() and
        "send 'book appointment'" not in reply.lower(),
        f"bot said: {reply!r}",
    )
    check(
        "Reply contains the 1/2 menu (clarify_intent)",
        "1." in reply and "2." in reply,
        f"bot said: {reply!r}",
    )
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state reset to INIT after greeting from CANCELLED",
        loaded is not None and loaded.state == "INIT",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — CANCELLED + greeting + HAS active booking → existing-booking menu
#          (unchanged behaviour — re-entry must still detect active booking)
# ─────────────────────────────────────────────────────────────────────────────

def test_6_cancelled_greeting_has_active_booking_unchanged() -> None:
    section("TEST 6: CANCELLED + 'Hello' + HAS active booking → existing-booking menu")

    cr = MockConversationRepo()
    uid = "telegram:test_cancelled_active"
    cr.seed_state(uid, "CANCELLED")

    br = _make_booking_repo(has_active_booking=True, known_name="Vineeth Raja")
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("GREETING"))

    reply = turn(sm, uid, "Hello")

    check(
        "Reply shows existing-booking menu",
        "already have" in reply.lower() or
        "Booking Number" in reply or
        "existing" in reply.lower(),
        f"bot said: {reply!r}",
    )
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state is ASK_EXISTING_BOOKING_ACTION",
        loaded is not None and loaded.state == "ASK_EXISTING_BOOKING_ACTION",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — CANCELLED + "book appointment" → ASK_NAME (existing behaviour unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def test_7_cancelled_book_intent_goes_to_ask_name_unchanged() -> None:
    section("TEST 7: CANCELLED + 'book appointment' → ASK_NAME (existing behaviour unchanged)")

    cr = MockConversationRepo()
    uid = "telegram:test_cancelled_book"
    cr.seed_state(uid, "CANCELLED")

    br = _make_booking_repo(has_active_booking=False, known_name=None)
    sch = _make_scheduling_repo()
    sm = _make_session_manager(br, sch, cr, llm=_make_llm("BOOK_APPOINTMENT"))

    reply = turn(sm, uid, "book appointment")

    check(
        "Reply asks for patient name (ASK_NAME)",
        "patient full name" in reply.lower() or "share the patient" in reply.lower(),
        f"bot said: {reply!r}",
    )
    loaded = cr.load_session(uid, ttl_minutes=120)
    check(
        "Session state is ASK_NAME",
        loaded is not None and loaded.state == "ASK_NAME",
        f"state was: {loaded.state if loaded else 'None'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# REGRESSION TEST — Prove old behaviour was broken
# ─────────────────────────────────────────────────────────────────────────────

def test_regression_old_completed_hint_was_wrong() -> None:
    section("REGRESSION: Old code showed completed_hint for 'Hello' in COMPLETED — proves fix needed")

    # Simulate OLD behaviour by directly calling get_message on templates
    from src.messages.templates import get_message
    old_completed_hint = get_message("en", "completed_hint")
    old_cancelled_hint = get_message("en", "cancelled_hint")

    check(
        "completed_hint text is the bad message we no longer show",
        "appointment flow is complete" in old_completed_hint.lower(),
        f"text: {old_completed_hint!r}",
    )
    check(
        "cancelled_hint text is the bad message we no longer show",
        "process is ended" in old_cancelled_hint.lower(),
        f"text: {old_cancelled_hint!r}",
    )

    # Now confirm the NEW code does NOT return these for "Hello"
    cr = MockConversationRepo()
    uid = "telegram:regression_test"
    cr.seed_state(uid, "COMPLETED")
    br = _make_booking_repo(has_active_booking=False)
    sm = _make_session_manager(br, _make_scheduling_repo(), cr, llm=_make_llm("GREETING"))
    reply = turn(sm, uid, "Hello")

    check(
        "NEW code: 'Hello' in COMPLETED does NOT return completed_hint",
        old_completed_hint not in reply,
        f"bot said: {reply!r}",
    )

    cr2 = MockConversationRepo()
    cr2.seed_state(uid, "CANCELLED")
    br2 = _make_booking_repo(has_active_booking=False)
    sm2 = _make_session_manager(br2, _make_scheduling_repo(), cr2, llm=_make_llm("GREETING"))
    reply2 = turn(sm2, uid, "Hello")

    check(
        "NEW code: 'Hello' in CANCELLED does NOT return cancelled_hint",
        old_cancelled_hint not in reply2,
        f"bot said: {reply2!r}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_1_completed_greeting_no_active_booking()
    test_2_completed_greeting_has_active_booking_unchanged()
    test_3_completed_book_intent_no_double_db_call()
    test_4_completed_book_intent_has_active_booking_unchanged()
    test_5_cancelled_greeting_no_active_booking()
    test_6_cancelled_greeting_has_active_booking_unchanged()
    test_7_cancelled_book_intent_goes_to_ask_name_unchanged()
    test_regression_old_completed_hint_was_wrong()

    print(f"\n{'═'*65}")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed")
    print(f"{'═'*65}")
    sys.exit(0 if FAIL == 0 else 1)
