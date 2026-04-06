"""
PRODUCTION BEHAVIOUR TESTS
===========================
These tests drive the bot the same way Telegram does:

  incoming webhook body  →  session_manager.get_or_create()
                         →  fsm.handle()
                         →  session_manager.save()
                         →  captured bot reply

No subclassing of any production code.
No inspection of FSM internal state variables.
The only assertions are on the TEXT the bot sends back to the user,
which is exactly what matters in production.

The MockConversationRepo stores SessionSnapshot rows in a plain Python dict —
exactly what MySQL would do — and the real SessionManager.save() /
get_or_create() code path runs on every turn.

Each test also has a REGRESSION SECTION that shows exactly what goes wrong
when the snapshot is missing the fields that were absent before the fix.

Run:
  $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_behaviour_contract.py
"""

import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.conversation_repository import SessionSnapshot
from src.repositories.scheduling_repository import ClinicOption
from src.session_store import SessionManager

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
# Real-ish ConversationRepository backed by a dict
# (no subclassing of production code — we implement the same interface from scratch)
# ─────────────────────────────────────────────────────────────────────────────

class MockConversationRepo:
    """
    Stores serialised session rows exactly as MySQL would, using a plain dict.
    Implements the same interface that SessionManager calls: save_session()
    and load_session().  Does NOT inherit from ConversationRepository.
    """

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}

    # Called by SessionManager.save()
    def save_session(
        self,
        *,
        user_id: str,
        state: str,
        context: dict,
        response_language: str,
        language_locked: bool,
        language_turn_count: int,
        init_unclear_count: int,
        in_edit_flow: bool,
        doctor_id: Optional[int],
        admin_id: Optional[int],
        fsm_extra_json: Optional[str] = None,
    ) -> None:
        self._rows[user_id] = {
            "user_id": user_id,
            "state": state,
            "context_json": json.dumps(context, ensure_ascii=False),
            "response_language": response_language,
            "language_locked": language_locked,
            "language_turn_count": language_turn_count,
            "init_unclear_count": init_unclear_count,
            "in_edit_flow": in_edit_flow,
            "doctor_id": doctor_id,
            "admin_id": admin_id,
            "fsm_extra_json": fsm_extra_json,
            "updated_at": datetime.utcnow(),
        }

    # Called by SessionManager._load_or_create_fsm()
    def load_session(self, user_id: str, ttl_minutes: int) -> Optional[SessionSnapshot]:
        row = self._rows.get(user_id)
        if not row:
            return None
        return SessionSnapshot(
            user_id=row["user_id"],
            state=row["state"],
            context_json=row["context_json"],
            response_language=row["response_language"],
            language_locked=bool(row["language_locked"]),
            language_turn_count=int(row["language_turn_count"] or 0),
            init_unclear_count=int(row["init_unclear_count"] or 0),
            in_edit_flow=bool(row["in_edit_flow"]),
            doctor_id=row["doctor_id"],
            admin_id=row["admin_id"],
            updated_at=row["updated_at"],
            fsm_extra_json=row.get("fsm_extra_json"),
        )

    def ensure_schema(self) -> None:
        pass

    def raw_row(self, user_id: str) -> Optional[dict]:
        """Direct access to the stored JSON — used in regression checks."""
        return deepcopy(self._rows.get(user_id))

    def corrupt_row_drop_extra(self, user_id: str) -> None:
        """
        Simulate what the OLD code stored (before fix): remove fsm_extra_json.
        Used in regression sections to prove the bug path.
        """
        if user_id in self._rows:
            self._rows[user_id]["fsm_extra_json"] = None


# ─────────────────────────────────────────────────────────────────────────────
# Bot / repo setup helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_booking_repo(known_name: Optional[str] = "Vineeth Raja",
                       known_phone: Optional[str] = "9876543210") -> MagicMock:
    repo = MagicMock()
    repo.list_active_appointments_by_chat_user_id.return_value = []
    repo.list_active_appointments_by_phone_number.return_value = []
    repo.find_patient_name_by_chat_user_id.return_value = known_name
    repo.find_patient_name_by_phone_number.return_value = known_name
    repo.find_patient_phone_by_chat_user_id.return_value = known_phone
    repo.get_doctor_display_name.return_value = "Dr. Sanjay"
    repo.default_admin_id.return_value = 1
    sv = MagicMock()
    sv.ok = True
    sv.appointment_id = 99
    sv.queue_number = None
    repo.save_confirmed_appointment.return_value = sv
    return repo


def _make_scheduling_repo(many_time_slots: bool = False) -> MagicMock:
    sch = MagicMock()
    sch.default_doctor_id.return_value = 1
    sch.default_doctor_id_by_username.return_value = 1
    sch.default_doctor_id_by_phone.return_value = 1
    sch.doctor_accept_days.return_value = 2
    sch.list_available_dates.return_value = ["2026-03-10", "2026-03-11"]
    if many_time_slots:
        # 27 slots across 9 hours → triggers the PERIOD picker (Morning/Afternoon/Evening)
        sch.list_available_times.return_value = [
            f"{h:02d}:{m:02d}" for h in range(9, 18) for m in (0, 20, 40)
        ]
    else:
        sch.list_available_times.return_value = ["10:00", "10:20", "10:40", "11:00"]
    sch.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Clinic", location="Noida", today_slots=5)
    ]
    return sch


def _make_llm() -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    llm.generate.return_value = "BOOK_APPOINTMENT"
    return llm


def _make_session_manager(
    booking_repo: MagicMock,
    scheduling_repo: MagicMock,
    conversation_repo: MockConversationRepo,
) -> SessionManager:
    """
    Real SessionManager — same class used in main.py.
    redis_client=None so it falls back to conversation_repo (our mock DB).
    """
    return SessionManager(
        llm_client=_make_llm(),
        enable_llm_polish=False,
        booking_repository=booking_repo,
        scheduling_repository=scheduling_repo,
        conversation_repository=conversation_repo,
        redis_client=None,   # force DB path so every turn does a real save+load
    )


def turn(sm: SessionManager, uid: str, text: str) -> str:
    """
    One complete production round-trip:
      get_or_create → handle → save → return bot reply

    This is exactly what _process_turn() in main.py does.
    """
    fsm = sm.get_or_create(uid)
    reply = fsm.handle(text)
    sm.save(uid, fsm)
    return reply


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Known patient: bot must NOT ask for name or phone
# ─────────────────────────────────────────────────────────────────────────────

def test_1_known_patient_skips_name_and_phone() -> None:
    section("TEST 1: Known patient — no name/phone prompt after 'for self'")

    br = _make_booking_repo(known_name="Vineeth Raja", known_phone="9876543210")
    cr = MockConversationRepo()
    sm = _make_session_manager(br, _make_scheduling_repo(), cr)
    uid = "telegram:8299824956"

    r1 = turn(sm, uid, "/start")
    check("Greeting names the patient",
          "Vineeth" in r1,
          f"bot said: {r1!r}")

    r2 = turn(sm, uid, "1")   # book appointment
    check("After '1': shows booking-for options (self/other)",
          any(w in r2.lower() for w in ("self", "myself", "1.", "1)")),
          f"bot said: {r2!r}")

    r3 = turn(sm, uid, "1")   # for self
    # ── WHAT THE BOT MUST SAY ──────────────────────────────────────────────
    # It already knows the name and phone from DB.
    # It must acknowledge the name and jump straight to clinic selection.
    check("Bot confirms the patient's own name",
          "Vineeth" in r3,
          f"bot said: {r3!r}")
    check("Bot shows clinic prompt",
          "clinic" in r3.lower() or "City Clinic" in r3,
          f"bot said: {r3!r}")
    check("Bot does NOT ask 'please share name'",
          "share" not in r3.lower() or "name" not in r3.lower(),
          f"bot asked for name when it shouldn't: {r3!r}")
    check("Bot does NOT ask for phone number",
          "contact number" not in r3.lower() and "phone" not in r3.lower(),
          f"bot asked for phone when it shouldn't: {r3!r}")

    # ── REGRESSION: what lets the behaviour stay correct even without snapshot ─
    # The fix has TWO layers:
    #   Layer 1 — fsm_extra_json in snapshot carries known_patient_name (avoids DB call per turn)
    #   Layer 2 — _hydrate_known_patient_name() is called at top of EVERY handle() call
    # Before the fix, layer 2 did NOT exist: it was only called inside
    # _handle_init_booking_prefill() (INIT state only).  So once the session was
    # past INIT, known_patient_name was permanently None.
    # We verify layer 2 works as safety net: even without the snapshot field,
    # the DB is re-queried and the correct behaviour is preserved.
    section("  REGRESSION 1: no snapshot field → DB re-hydration must have fired")
    br2 = _make_booking_repo(known_name="Vineeth Raja", known_phone="9876543210")
    cr2 = MockConversationRepo()
    sm2 = _make_session_manager(br2, _make_scheduling_repo(), cr2)
    turn(sm2, uid, "/start")
    turn(sm2, uid, "1")
    cr2.corrupt_row_drop_extra(uid)          # known_patient_name removed from snapshot
    br2.find_patient_name_by_chat_user_id.reset_mock()
    r3b = turn(sm2, uid, "1")               # FSM rebuilt without known_patient_name
    # Layer 2 must have called the DB to re-hydrate and the clinic is still shown
    db_was_called = br2.find_patient_name_by_chat_user_id.called
    check("Layer 2: DB was re-queried to get patient name (safety net active)",
          db_was_called,
          "find_patient_name_by_chat_user_id was never called on the post-corruption turn")
    check("Behaviour still correct despite missing snapshot field",
          "City Clinic" in r3b or "clinic" in r3b.lower(),
          f"reply={r3b!r}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Pressing 0 (back) from clinic: must return to booking-for, NOT phone
# ─────────────────────────────────────────────────────────────────────────────

def test_2_go_back_from_clinic_returns_to_booking_for() -> None:
    section("TEST 2: '0' from clinic → bot re-shows self/other choice, not phone prompt")

    br = _make_booking_repo()
    cr = MockConversationRepo()
    sm = _make_session_manager(br, _make_scheduling_repo(), cr)
    uid = "telegram:8299824956"

    turn(sm, uid, "/start")
    turn(sm, uid, "1")    # INIT → booking intent
    turn(sm, uid, "1")    # for self → clinic shown

    r_back = turn(sm, uid, "0")

    check("Bot re-shows the self/other choice",
          any(w in r_back.lower() for w in ("self", "myself", "1.", "1)")),
          f"bot said: {r_back!r}")
    check("Bot does NOT ask for phone number after going back",
          "contact number" not in r_back.lower()
          and "10-digit" not in r_back.lower()
          and "share" not in r_back.lower(),
          f"bot asked for phone after back-from-clinic: {r_back!r}")

    # ── REGRESSION ────────────────────────────────────────────────────────
    section("  REGRESSION 2: old snapshot drops booking_for_self → wrong back destination")
    br2 = _make_booking_repo()
    cr2 = MockConversationRepo()
    sm2 = _make_session_manager(br2, _make_scheduling_repo(), cr2)
    turn(sm2, uid, "/start")
    turn(sm2, uid, "1")
    turn(sm2, uid, "1")
    cr2.corrupt_row_drop_extra(uid)          # booking_for_self is now None
    r_bad = turn(sm2, uid, "0")
    phone_asked = "contact number" in r_bad.lower() or "10-digit" in r_bad.lower()
    check("OLD behaviour: bot asked for phone (confirms bug existed)",
          phone_asked,
          f"(if this fails the regression section is no longer valid) reply={r_bad!r}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Time slot selection must not loop (period + slot survive serialisation)
# ─────────────────────────────────────────────────────────────────────────────

def test_3_time_slot_selection_does_not_loop() -> None:
    section("TEST 3: Picking a time slot reaches confirmation — no infinite loop")

    br = _make_booking_repo()
    cr = MockConversationRepo()
    sm = _make_session_manager(br, _make_scheduling_repo(many_time_slots=True), cr)
    uid = "telegram:8299824956"

    turn(sm, uid, "/start")
    turn(sm, uid, "1")    # intent
    turn(sm, uid, "1")    # for self → clinic
    turn(sm, uid, "1")    # clinic → date
    turn(sm, uid, "1")    # date → time

    # With many slots (>4 hours) the bot shows Morning/Afternoon/Evening first
    r_period = turn(sm, uid, "something")
    check("Bot shows period options (Morning/Afternoon/Evening)",
          any(w in r_period.lower() for w in ("morning", "afternoon", "evening")),
          f"bot said: {r_period!r}")

    # User picks period "1" — bot should now show specific hour slots
    r_slots = turn(sm, uid, "1")
    showed_slots = "1." in r_slots or "1)" in r_slots
    showed_period_again = (
        "morning" in r_slots.lower()
        or "afternoon" in r_slots.lower()
        or "evening" in r_slots.lower()
    )
    check("After period choice: bot shows specific hour slots (not period again)",
          showed_slots and not showed_period_again,
          f"bot said: {r_slots!r}")

    # User picks slot "1" — bot should move to CONFIRM, not loop back
    r_confirm = turn(sm, uid, "1")
    # Confirmation summary contains Name: or appointment time — either proves we moved on
    reached_confirm = (
        "name:" in r_confirm.lower()
        or "confirm" in r_confirm.lower()
        or "summary" in r_confirm.lower()
        or "10:" in r_confirm          # a time from the mock data
        or "appointment" in r_confirm.lower()
    )
    looped_to_period = any(
        w in r_confirm.lower() for w in ("morning", "afternoon", "evening")
    )
    check("After slot pick: bot shows confirmation (not looping to period again)",
          reached_confirm and not looped_to_period,
          f"bot said: {r_confirm!r}")

    # ── REGRESSION ────────────────────────────────────────────────────────
    # The bug: on each turn the FSM is rebuilt from snapshot.
    # Without selected_time_period + time_slot_options_cache in the snapshot,
    # the NEXT turn after the user picks a period would:
    #   1. Repopulate time_hour_options_cache via _load_time_options (many hours)
    #   2. See time_slot_options_cache == [] (wiped by corruption)
    #   3. See selected_time_period == None (wiped by corruption)
    #   4. Call _resolve_time_period_choice("hello", "hello") → None (no match)
    #   5. Show the PERIOD prompt AGAIN → infinite loop
    # We use "hello" (not "1") because "1" would accidentally auto-select period #1.
    section("  REGRESSION 3: old snapshot missing period/cache → period prompt repeats")
    br2 = _make_booking_repo()
    cr2 = MockConversationRepo()
    sm2 = _make_session_manager(br2, _make_scheduling_repo(many_time_slots=True), cr2)
    turn(sm2, uid, "/start")
    turn(sm2, uid, "1")
    turn(sm2, uid, "1")
    turn(sm2, uid, "1")
    turn(sm2, uid, "1")
    turn(sm2, uid, "hello")    # arrives at ASK_TIME → period options shown
    turn(sm2, uid, "1")        # picks period 1 (Morning) → slot list sent and SAVED
    cr2.corrupt_row_drop_extra(uid)   # wipe selected_time_period + slot cache
    # User now tries to pick slot 1 from the list they just saw.
    # With OLD code: period cache gone → bot shows period prompt AGAIN.
    r_bad = turn(sm2, uid, "hello")   # non-digit so _resolve_time_period_choice returns None
    looped = any(w in r_bad.lower() for w in ("morning", "afternoon", "evening"))
    check("OLD behaviour: period prompt repeats after corruption (confirms bug existed)",
          looped,
          f"reply={r_bad!r}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Full booking: known Telegram patient, complete the whole flow
#          Bot messages, not state variables
# ─────────────────────────────────────────────────────────────────────────────

def test_4_full_booking_known_patient() -> None:
    section("TEST 4: Complete booking, known patient — messages are correct end-to-end")

    br = _make_booking_repo()
    cr = MockConversationRepo()
    sm = _make_session_manager(br, _make_scheduling_repo(), cr)
    uid = "telegram:8299824956"

    # /start
    r = turn(sm, uid, "/start")
    check("/start — bot greets by name", "Vineeth" in r, f"reply={r!r}")

    # Book appointment
    r = turn(sm, uid, "1")
    check("After '1' — bot asks self-or-other", "self" in r.lower(), f"reply={r!r}")

    # For self
    r = turn(sm, uid, "1")
    check("After self — bot shows clinic", "clinic" in r.lower() or "City Clinic" in r, f"reply={r!r}")
    check("After self — no name question", "please share" not in r.lower() or "name" not in r.lower(), f"reply={r!r}")
    check("After self — no phone question", "contact number" not in r.lower(), f"reply={r!r}")

    # Pick clinic
    r = turn(sm, uid, "1")
    check("After clinic — bot shows dates", "march" in r.lower() or "2026" in r or "1." in r, f"reply={r!r}")

    # Pick date
    r = turn(sm, uid, "1")
    check("After date — bot shows time options", "10:" in r or "1." in r, f"reply={r!r}")

    # Navigate time selection (may need 1–2 picks depending on slot count)
    for attempt in range(4):
        r = turn(sm, uid, "1")
        if any(kw in r.lower() for kw in ("confirm", "name:", "appointment")):
            break

    check("After time pick — bot shows summary with patient name",
          "Vineeth" in r or "name:" in r.lower(),
          f"reply={r!r}")
    check("After time pick — bot shows clinic name",
          "City Clinic" in r, f"reply={r!r}")

    # Confirm
    r = turn(sm, uid, "1")
    check("After confirm — bot confirms booking",
          "successfully" in r.lower() or "booked" in r.lower() or "confirmed" in r.lower(),
          f"reply={r!r}")
    check("After confirm — appointment ID in reply", "99" in r, f"reply={r!r}")
    check("After confirm — contact number present (not None)",
          "Contact: None" not in r and "9876543210" in r,
          f"reply={r!r}")
    check("After confirm — no leftover name-ask or phone-ask",
          "please share" not in r.lower(), f"reply={r!r}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Unknown patient must still be asked for name and phone
# ─────────────────────────────────────────────────────────────────────────────

def test_5_unknown_patient_is_asked_name() -> None:
    section("TEST 5: Unknown patient — bot MUST ask for name, then phone")

    br = _make_booking_repo(known_name=None, known_phone=None)
    br.find_patient_name_by_chat_user_id.return_value = None
    cr = MockConversationRepo()
    sm = _make_session_manager(br, _make_scheduling_repo(), cr)
    uid = "telegram:0000000000"

    r = turn(sm, uid, "/start")
    check("Greeting for unknown patient (generic, no name)", "Vineeth" not in r, f"reply={r!r}")

    turn(sm, uid, "1")    # book intent

    r = turn(sm, uid, "1")    # for self
    check("Bot asks for patient name", "name" in r.lower(), f"reply={r!r}")
    check("Bot does NOT jump to clinic", "clinic" not in r.lower(), f"reply={r!r}")

    r = turn(sm, uid, "Rohit Sharma")
    check("After name — bot asks for contact number",
          "contact" in r.lower() or "number" in r.lower() or "phone" in r.lower(),
          f"reply={r!r}")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 65)
    print("  PRODUCTION BEHAVIOUR CONTRACT TESTS")
    print("  (real SessionManager + real save/load cycle, mock repos only)")
    print("=" * 65)

    test_1_known_patient_skips_name_and_phone()
    test_2_go_back_from_clinic_returns_to_booking_for()
    test_3_time_slot_selection_does_not_loop()
    test_4_full_booking_known_patient()
    test_5_unknown_patient_is_asked_name()

    print(f"\n{'='*65}")
    print(f"  RESULT: {PASS} passed, {FAIL} failed")
    print(f"{'='*65}")
    sys.exit(0 if FAIL == 0 else 1)
