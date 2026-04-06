"""
Real session-reconstruction tests.
Every "turn" destroys the FSM and rebuilds it from snapshot — exactly as
session_manager does in production.  Uses the updated _fsm_extra_dict logic.

Run:
  $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_session_snapshot_real.py
"""

import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption
from src.session_store import SessionManager

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  ← {detail}" if detail else ""))


# ─────────────────────────────────────────────
# Shared mock factories
# ─────────────────────────────────────────────

def _booking_repo(known_name: Optional[str] = "Vineeth Raja",
                  known_phone: Optional[str] = "9876543210"):
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


def _scheduling_repo(many_slots=False):
    sch = MagicMock()
    sch.default_doctor_id.return_value = 1
    sch.default_doctor_id_by_username.return_value = 1
    sch.default_doctor_id_by_phone.return_value = 1
    sch.doctor_accept_days.return_value = 2
    sch.list_available_dates.return_value = ["2026-03-10", "2026-03-11"]
    # many_slots=True gives >4 hours → triggers period selection (Morning/Afternoon/…)
    if many_slots:
        sch.list_available_times.return_value = [
            f"{h:02d}:{m:02d}" for h in range(9, 18) for m in (0, 20, 40)
        ]   # 27 slots spanning 9 hours
    else:
        sch.list_available_times.return_value = ["10:00", "10:20", "10:40",
                                                  "11:00", "11:20"]
    sch.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Clinic",
                     location="Noida", today_slots=5)
    ]
    return sch


def _llm():
    llm = MagicMock(spec=LLMClient)
    llm.generate.return_value = "BOOK_APPOINTMENT"
    return llm


# ─────────────────────────────────────────────
# SessionManager with in-memory (no-Redis, no-DB) snapshot
# ─────────────────────────────────────────────

class _InMemorySessionManager(SessionManager):
    """
    Subclass that replaces the DB and Redis with a dict so we can run full
    multi-turn sessions without any real infrastructure.
    """
    def __init__(self, **kw):
        super().__init__(**kw)
        self._store: dict[str, dict] = {}

    def save(self, user_id: str, fsm=None) -> None:
        current_fsm = fsm or self._load_or_create_fsm(user_id=user_id)
        # Replicate _save_redis_snapshot logic into our in-memory store
        payload = {
            "state": current_fsm.state,
            "context": dict(current_fsm.context.__dict__),
            "response_language": current_fsm.response_language,
            "language_locked": bool(current_fsm.language_locked),
            "language_turn_count": int(current_fsm.language_turn_count or 0),
            "init_unclear_count": int(current_fsm.init_unclear_count or 0),
            "in_edit_flow": bool(current_fsm.in_edit_flow),
            "doctor_id": current_fsm.doctor_id,
            "admin_id": current_fsm.admin_id,
        }
        payload.update(self._fsm_extra_dict(current_fsm))
        self._store[user_id] = payload

    def _load_or_create_fsm(self, user_id: str) -> AppointmentFSM:
        fsm = AppointmentFSM(
            llm_client=self.llm_client,
            enable_llm_polish=self.enable_llm_polish,
            booking_repository=self.booking_repository,
            scheduling_repository=self.scheduling_repository,
            chat_phone_number=user_id,
            bot_whatsapp_number=self.bot_whatsapp_number,
        )
        snap = self._store.get(user_id)
        if snap:
            self._apply_snapshot_to_fsm(fsm=fsm, snapshot=snap)
        return fsm


def _make_sm(user_id="telegram:8299824956",
             known_name="Vineeth Raja",
             known_phone="9876543210",
             many_slots=False):
    sm = _InMemorySessionManager(
        llm_client=_llm(),
        enable_llm_polish=False,
        booking_repository=_booking_repo(known_name=known_name,
                                          known_phone=known_phone),
        scheduling_repository=_scheduling_repo(many_slots=many_slots),
    )
    return sm


def _turn(sm, user_id, text):
    """One complete session-manager turn: load → handle → save → return reply."""
    fsm = sm.get_or_create(user_id)
    reply = fsm.handle(text)
    sm.save(user_id, fsm)
    return reply, fsm.state


# ─────────────────────────────────────────────
# TEST A: known patient → ASK_CLINIC (not ASK_NAME)
# ─────────────────────────────────────────────
def test_a_known_patient_skips_name_phone():
    print("\n[TEST A] Known patient self-booking skips ASK_NAME and ASK_PHONE")
    uid = "telegram:8299824956"
    sm = _make_sm(uid)

    r, s = _turn(sm, uid, "/start")
    check("  /start: greeting contains known name", "Vineeth" in r, f"reply={r[:200]!r}")
    check("  state=INIT after /start", s == "INIT", f"state={s}")

    r, s = _turn(sm, uid, "1")
    check("  '1' at INIT → ASK_BOOKING_FOR", s == "ASK_BOOKING_FOR", f"state={s}")

    r, s = _turn(sm, uid, "1")   # "for self"
    check("  '1' at ASK_BOOKING_FOR → ASK_CLINIC (not ASK_NAME)",
          s == "ASK_CLINIC", f"state={s}, reply={r[:250]!r}")
    check("  Name acknowledged in reply", "Vineeth" in r, f"reply={r[:250]!r}")
    check("  ASK_NAME never visited", "Your name?" not in r and "ask_name" not in r.lower())


# ─────────────────────────────────────────────
# TEST B: booking_for_self persists → go-back from ASK_CLINIC → ASK_BOOKING_FOR
# ─────────────────────────────────────────────
def test_b_go_back_from_clinic_known_patient():
    print("\n[TEST B] Go-back from ASK_CLINIC: known patient self → ASK_BOOKING_FOR (not ASK_PHONE)")
    uid = "telegram:111"
    sm = _make_sm(uid)

    _turn(sm, uid, "/start")
    _turn(sm, uid, "1")    # INIT → ASK_BOOKING_FOR
    _turn(sm, uid, "1")    # ASK_BOOKING_FOR → ASK_CLINIC

    r, s = _turn(sm, uid, "0")   # go back from ASK_CLINIC
    check("  '0' at ASK_CLINIC → ASK_BOOKING_FOR",
          s == "ASK_BOOKING_FOR", f"state={s}, reply={r[:200]!r}")
    check("  NOT routed to ASK_PHONE", s != "ASK_PHONE", f"state={s}")


# ─────────────────────────────────────────────
# TEST C: time slot cache persists across turns (the infinite-loop bug)
# ─────────────────────────────────────────────
def test_c_time_slot_cache_persists():
    print("\n[TEST C] Time slot selection: cache survives session reconstruction")
    uid = "telegram:222"
    sm = _make_sm(uid)

    _turn(sm, uid, "/start")
    _turn(sm, uid, "1")    # INIT → ASK_BOOKING_FOR
    _turn(sm, uid, "1")    # ASK_BOOKING_FOR → ASK_CLINIC
    _turn(sm, uid, "1")    # ASK_CLINIC → ASK_DATE
    _turn(sm, uid, "1")    # ASK_DATE → ASK_TIME

    # First input in ASK_TIME: should build the slot list and show it.
    # (<= 4 hours with our mock → goes straight to slot list, no period step)
    r1, s1 = _turn(sm, uid, "1")
    check("  After first '1' in ASK_TIME: state", s1 in ("ASK_TIME", "CONFIRM"),
          f"state={s1}")

    if s1 == "ASK_TIME":
        # Slot list was shown; picking "1" again should advance to CONFIRM
        # NOT loop back to show the slot list again
        r2, s2 = _turn(sm, uid, "1")
        check("  Second '1' in ASK_TIME → CONFIRM (not stuck in loop)",
              s2 == "CONFIRM", f"state={s2}, reply={r2[:200]!r}")
    else:
        check("  Went straight to CONFIRM on first '1'", True)


# ─────────────────────────────────────────────
# TEST D: period selection persists (many slots → 2-step time picker)
# ─────────────────────────────────────────────
def test_d_period_selection_persists():
    print("\n[TEST D] Period selection (morning/afternoon) persists across turns")
    uid = "telegram:333"
    sm = _make_sm(uid, many_slots=True)   # >4 unique hours → period prompt

    _turn(sm, uid, "/start")
    _turn(sm, uid, "1")
    _turn(sm, uid, "1")    # ASK_BOOKING_FOR → ASK_CLINIC
    _turn(sm, uid, "1")    # ASK_CLINIC → ASK_DATE
    _turn(sm, uid, "1")    # ASK_DATE → ASK_TIME

    # First turn in ASK_TIME: should show period prompt (Morning/Afternoon/Evening)
    r1, s1 = _turn(sm, uid, "anything")
    check("  ASK_TIME with many slots stays in ASK_TIME", s1 == "ASK_TIME",
          f"state={s1}")

    # Select period "1" (Morning) — next turn must show HOUR slots, not period again
    r2, s2 = _turn(sm, uid, "1")
    check("  After period choice: still ASK_TIME (showing hour slots)",
          s2 == "ASK_TIME", f"state={s2}")
    # The reply should NOT contain the period options again (Morning/Afternoon/Evening)
    period_words = any(w in r2.lower() for w in ("morning", "afternoon", "evening"))
    slot_digit = "1." in r2 or "1)" in r2
    check("  Period prompt NOT repeated after period selected",
          not period_words or slot_digit,
          f"reply={r2[:300]!r}")

    # Now pick a slot from the hour list → should advance to CONFIRM
    r3, s3 = _turn(sm, uid, "1")
    check("  Slot pick → CONFIRM", s3 == "CONFIRM",
          f"state={s3}, reply={r3[:200]!r}")


# ─────────────────────────────────────────────
# TEST E: unknown patient still goes through ASK_NAME
# ─────────────────────────────────────────────
def test_e_unknown_patient_asks_name():
    print("\n[TEST E] Unknown patient: must go through ASK_NAME")
    uid = "telegram:999"
    sm = _make_sm(uid, known_name=None, known_phone=None)
    sm.booking_repository.find_patient_name_by_chat_user_id.return_value = None

    _turn(sm, uid, "/start")
    _turn(sm, uid, "1")    # INIT → ASK_BOOKING_FOR

    r, s = _turn(sm, uid, "1")   # self, unknown → must ask name
    check("  Unknown patient '1' → ASK_NAME", s == "ASK_NAME",
          f"state={s}, reply={r[:200]!r}")


# ─────────────────────────────────────────────
# TEST F: full happy-path booking for known Telegram patient
# ─────────────────────────────────────────────
def test_f_full_booking_flow_known_patient():
    print("\n[TEST F] Full booking flow: known Telegram patient, end-to-end")
    uid = "telegram:8299824956"
    sm = _make_sm(uid)

    _turn(sm, uid, "/start")
    _turn(sm, uid, "1")          # INIT → ASK_BOOKING_FOR
    r, s = _turn(sm, uid, "1")   # ASK_BOOKING_FOR → ASK_CLINIC
    check("  At ASK_CLINIC", s == "ASK_CLINIC", f"state={s}")

    r, s = _turn(sm, uid, "1")   # ASK_CLINIC → ASK_DATE
    check("  At ASK_DATE", s == "ASK_DATE", f"state={s}")

    r, s = _turn(sm, uid, "1")   # ASK_DATE → ASK_TIME
    check("  At ASK_TIME", s == "ASK_TIME", f"state={s}")

    # Keep answering "1" until we leave ASK_TIME (max 3 iterations to avoid hang)
    for _ in range(3):
        r, s = _turn(sm, uid, "1")
        if s != "ASK_TIME":
            break
    check("  Left ASK_TIME → CONFIRM", s == "CONFIRM", f"state={s}")

    r, s = _turn(sm, uid, "1")   # CONFIRM → COMPLETED
    check("  CONFIRM → COMPLETED", s == "COMPLETED", f"state={s}")
    check("  Success message", "successfully" in r.lower() or "booked" in r.lower(),
          f"reply={r[:300]!r}")
    check("  Appointment ID present", "99" in r, f"reply={r[:300]!r}")
    check("  Contact not None", "Contact: None" not in r, f"reply={r[:300]!r}")


if __name__ == "__main__":
    print("=" * 65)
    print("  SESSION SNAPSHOT REAL MULTI-TURN TESTS")
    print("=" * 65)
    test_a_known_patient_skips_name_phone()
    test_b_go_back_from_clinic_known_patient()
    test_c_time_slot_cache_persists()
    test_d_period_selection_persists()
    test_e_unknown_patient_asks_name()
    test_f_full_booking_flow_known_patient()
    print(f"\n  RESULT: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
