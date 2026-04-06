"""
REAL multi-turn known-patient test.

Unlike test_recent_ux_changes.py (which keeps one FSM object across all steps),
this test simulates what session_manager actually does:

  Each "turn" = destroy FSM, recreate from snapshot dict, call handle().

This exercises the exact bug path that was hitting production:
  /start  → snapshot saved (known_patient_name NOT in DB snapshot)
  "1"     → NEW FSM from snapshot  →  admin_id/doctor_id restored, but
             known_patient_name = None  →  ASK_BOOKING_FOR picks up "1"
             → if known_patient_name is still None, falls through to ASK_NAME

Run:
  $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_known_patient_multiturm.py
"""

import sys, json
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


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Helpers to simulate real session_manager behaviour
# ---------------------------------------------------------------------------

def _make_booking_repo(known_name="Vineeth Raja", known_phone="9876543210"):
    repo = MagicMock()
    repo.list_active_appointments_by_chat_user_id.return_value = []
    repo.list_active_appointments_by_phone_number.return_value = []
    repo.find_patient_name_by_chat_user_id.return_value = known_name
    repo.find_patient_name_by_phone_number.return_value = known_name
    repo.find_patient_phone_by_chat_user_id.return_value = known_phone
    repo.get_doctor_display_name.return_value = "Sanjay"
    repo.default_admin_id.return_value = 1
    sv = MagicMock()
    sv.ok = True
    sv.appointment_id = 99
    sv.queue_number = None
    repo.save_confirmed_appointment.return_value = sv
    return repo


def _make_scheduling_repo():
    sch = MagicMock()
    sch.default_doctor_id.return_value = 1
    sch.default_doctor_id_by_username.return_value = 1
    sch.default_doctor_id_by_phone.return_value = 1
    sch.doctor_accept_days.return_value = 2
    sch.list_available_dates.return_value = ["2026-03-10", "2026-03-11"]
    sch.list_available_times.return_value = ["10:00", "10:20", "10:40"]
    sch.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Clinic", location="Noida", today_slots=5)
    ]
    return sch


def _snapshot_of(fsm: AppointmentFSM) -> dict:
    """
    Exactly what session_store._save_redis_snapshot (and save_session) persists.
    Notably: known_patient_name IS now included (after the fix to session_store).
    The DB path does NOT persit it, so we also test without it.
    """
    return {
        "state": fsm.state,
        "context": dict(fsm.context.__dict__),
        "response_language": fsm.response_language,
        "language_locked": bool(fsm.language_locked),
        "language_turn_count": int(fsm.language_turn_count or 0),
        "init_unclear_count": int(fsm.init_unclear_count or 0),
        "in_edit_flow": bool(fsm.in_edit_flow),
        "doctor_id": fsm.doctor_id,
        "admin_id": fsm.admin_id,
        "known_patient_name": fsm.known_patient_name,   # present in Redis snapshot
    }


def _snapshot_of_db_only(fsm: AppointmentFSM) -> dict:
    """DB snapshot – no known_patient_name column."""
    snap = _snapshot_of(fsm)
    snap.pop("known_patient_name", None)
    return snap


def _restore_fsm(snapshot: dict, user_id="telegram:8299824956",
                 known_name="Vineeth Raja", known_phone="9876543210") -> AppointmentFSM:
    """Recreate FSM from snapshot exactly as session_manager does."""
    llm = MagicMock(spec=LLMClient)
    llm.generate.return_value = "BOOK_APPOINTMENT"

    repo = _make_booking_repo(known_name=known_name, known_phone=known_phone)
    sch = _make_scheduling_repo()

    fsm = AppointmentFSM(
        llm_client=llm,
        chat_phone_number=user_id,
        booking_repository=repo,
        scheduling_repository=sch,
        enable_llm_polish=False,
        # doctor_id / admin_id NOT set here — must come from snapshot (like real code)
    )

    # Apply snapshot (mirrors session_store._apply_snapshot_to_fsm)
    ctx = snapshot.get("context") or {}
    if isinstance(ctx, str):
        ctx = json.loads(ctx or "{}")
    for k, v in ctx.items():
        if hasattr(fsm.context, k):
            setattr(fsm.context, k, v)
    fsm.state = str(snapshot.get("state") or "INIT")
    fsm.response_language = str(snapshot.get("response_language") or "en")
    fsm.language_locked = bool(snapshot.get("language_locked"))
    fsm.language_turn_count = int(snapshot.get("language_turn_count") or 0)
    fsm.init_unclear_count = int(snapshot.get("init_unclear_count") or 0)
    fsm.in_edit_flow = bool(snapshot.get("in_edit_flow"))
    did = snapshot.get("doctor_id")
    aid = snapshot.get("admin_id")
    fsm.doctor_id = int(did) if did is not None else None
    fsm.admin_id = int(aid) if aid is not None else None
    # known_patient_name from snapshot (will be None in DB-only case)
    kpn = snapshot.get("known_patient_name")
    fsm.known_patient_name = str(kpn) if kpn else None

    return fsm


# ---------------------------------------------------------------------------
# TEST A — with Redis snapshot (known_patient_name survives)
# ---------------------------------------------------------------------------
def test_a_redis_snapshot_known_patient():
    print("\n[TEST A] Multi-turn: Redis snapshot carries known_patient_name")

    user_id = "telegram:8299824956"

    # --- Turn 1: /start ---
    llm1 = MagicMock(spec=LLMClient)
    llm1.generate.return_value = "BOOK_APPOINTMENT"
    fsm1 = AppointmentFSM(
        llm_client=llm1,
        chat_phone_number=user_id,
        booking_repository=_make_booking_repo(),
        scheduling_repository=_make_scheduling_repo(),
        enable_llm_polish=False,
    )
    reply1 = fsm1.handle("/start")
    check("  /start: known patient greeted", "Vineeth" in reply1,
          f"reply={reply1[:200]!r}")
    check("  known_patient_name set after /start", fsm1.known_patient_name == "Vineeth Raja",
          f"got={fsm1.known_patient_name!r}")

    snap1 = _snapshot_of(fsm1)         # Redis snapshot (includes known_patient_name)
    check("  known_patient_name in Redis snap", snap1.get("known_patient_name") == "Vineeth Raja")

    # --- Turn 2: "1" → should go to ASK_BOOKING_FOR ---
    fsm2 = _restore_fsm(snap1, user_id=user_id)
    check("  FSM2 restored: state=INIT", fsm2.state == "INIT",
          f"state={fsm2.state!r}")
    check("  FSM2 restored: known_patient_name", fsm2.known_patient_name == "Vineeth Raja",
          f"got={fsm2.known_patient_name!r}")
    reply2 = fsm2.handle("1")
    check("  '1' at INIT → ASK_BOOKING_FOR", fsm2.state == "ASK_BOOKING_FOR",
          f"state={fsm2.state!r} reply={reply2[:120]!r}")

    snap2 = _snapshot_of(fsm2)

    # --- Turn 3: "1" (self) at ASK_BOOKING_FOR → should go to ASK_CLINIC, NOT ASK_NAME ---
    fsm3 = _restore_fsm(snap2, user_id=user_id)
    check("  FSM3 restored: state=ASK_BOOKING_FOR", fsm3.state == "ASK_BOOKING_FOR",
          f"state={fsm3.state!r}")
    check("  FSM3 known_patient_name present", bool(fsm3.known_patient_name),
          f"got={fsm3.known_patient_name!r}")
    reply3 = fsm3.handle("1")
    check("  '1' at ASK_BOOKING_FOR → ASK_CLINIC (not ASK_NAME)",
          fsm3.state == "ASK_CLINIC",
          f"state={fsm3.state!r} reply={reply3[:200]!r}")
    check("  Name acknowledged in reply", "Vineeth" in reply3,
          f"reply={reply3[:200]!r}")


# ---------------------------------------------------------------------------
# TEST B — DB-only snapshot (known_patient_name NOT in snapshot)
#           fix must re-hydrate from DB via _hydrate_known_patient_name()
# ---------------------------------------------------------------------------
def test_b_db_only_snapshot_known_patient():
    print("\n[TEST B] Multi-turn: DB snapshot (no known_patient_name) — must re-hydrate")

    user_id = "telegram:8299824956"

    # Turn 1
    llm1 = MagicMock(spec=LLMClient)
    llm1.generate.return_value = "BOOK_APPOINTMENT"
    fsm1 = AppointmentFSM(
        llm_client=llm1,
        chat_phone_number=user_id,
        booking_repository=_make_booking_repo(),
        scheduling_repository=_make_scheduling_repo(),
        enable_llm_polish=False,
    )
    fsm1.handle("/start")

    snap1_db = _snapshot_of_db_only(fsm1)   # simulates DB session — no known_patient_name
    check("  known_patient_name absent in DB snap",
          "known_patient_name" not in snap1_db)

    # Turn 2 from DB snapshot
    fsm2 = _restore_fsm(snap1_db, user_id=user_id)
    check("  FSM2 from DB: known_patient_name=None",
          fsm2.known_patient_name is None,
          f"got={fsm2.known_patient_name!r}")
    fsm2.handle("1")                         # INIT "1" → ASK_BOOKING_FOR
    check("  After '1': state=ASK_BOOKING_FOR", fsm2.state == "ASK_BOOKING_FOR",
          f"state={fsm2.state!r}")

    snap2_db = _snapshot_of_db_only(fsm2)

    # Turn 3 — THE CRITICAL ONE
    # FSM rebuilt from DB snapshot: known_patient_name=None
    # handle() must call _hydrate_known_patient_name() to re-fetch from DB
    fsm3 = _restore_fsm(snap2_db, user_id=user_id)
    check("  FSM3 from DB: known_patient_name=None before handle",
          fsm3.known_patient_name is None)

    reply3 = fsm3.handle("1")               # "self" at ASK_BOOKING_FOR

    check("  _hydrate_known_patient_name called on fsm3",
          fsm3.booking_repository.find_patient_name_by_chat_user_id.called,
          "find_patient_name_by_chat_user_id was never called")
    check("  '1' at ASK_BOOKING_FOR → ASK_CLINIC (not ASK_NAME)",
          fsm3.state == "ASK_CLINIC",
          f"state={fsm3.state!r} reply={reply3[:200]!r}")
    check("  Name acknowledged", "Vineeth" in reply3,
          f"reply={reply3[:200]!r}")


# ---------------------------------------------------------------------------
# TEST C — unknown patient (no DB match) must still ask name
# ---------------------------------------------------------------------------
def test_c_unknown_patient_still_asks_name():
    print("\n[TEST C] Multi-turn: unknown patient (no DB match) → must ask name")

    user_id = "telegram:9999999999"

    repo = _make_booking_repo(known_name=None, known_phone=None)
    repo.find_patient_name_by_chat_user_id.return_value = None

    llm1 = MagicMock(spec=LLMClient)
    llm1.generate.return_value = "BOOK_APPOINTMENT"
    fsm1 = AppointmentFSM(
        llm_client=llm1,
        chat_phone_number=user_id,
        booking_repository=repo,
        scheduling_repository=_make_scheduling_repo(),
        enable_llm_polish=False,
    )
    fsm1.handle("/start")
    check("  /start: known_patient_name is None for unknown", fsm1.known_patient_name is None,
          f"got={fsm1.known_patient_name!r}")

    snap = _snapshot_of_db_only(fsm1)
    fsm2 = _restore_fsm(snap, user_id=user_id, known_name=None, known_phone=None)
    fsm2.booking_repository.find_patient_name_by_chat_user_id.return_value = None
    fsm2.handle("1")                         # INIT → ASK_BOOKING_FOR
    snap2 = _snapshot_of_db_only(fsm2)

    fsm3 = _restore_fsm(snap2, user_id=user_id, known_name=None, known_phone=None)
    fsm3.booking_repository.find_patient_name_by_chat_user_id.return_value = None
    reply3 = fsm3.handle("1")               # self at ASK_BOOKING_FOR — unknown patient

    check("  Unknown patient → ASK_NAME",
          fsm3.state == "ASK_NAME",
          f"state={fsm3.state!r} reply={reply3[:150]!r}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 65)
    print("  REAL MULTI-TURN KNOWN-PATIENT TEST (session reconstruction)")
    print("=" * 65)
    test_a_redis_snapshot_known_patient()
    test_b_db_only_snapshot_known_patient()
    test_c_unknown_patient_still_asks_name()
    print(f"\n  RESULT: {PASS} passed, {FAIL} failed")
    import sys as _sys
    _sys.exit(0 if FAIL == 0 else 1)
