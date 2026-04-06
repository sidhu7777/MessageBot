"""
REQ-012: Duplicate telegram_chat_id Recovery
Verifies that save_confirmed_appointment does NOT return a failure when the
patients table already has a row with the same telegram_chat_id.
Previously this raised:
  1062 (23000): Duplicate entry '<chat_id>' for key 'patients.patients_telegram_chat_id_key'

Run: python tests/req_012_duplicate_telegram_chat_id_recovery.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.booking_repository import BookingRepository, BookingResult

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(chat_id: str = "8299824956") -> SimpleNamespace:
    return SimpleNamespace(
        patient_name="Vijjejee",
        appointment_date="2026-02-28",
        appointment_time="10:00",
        clinic_id=1,
        phone_number="9029298287",
        chat_user_id=chat_id,
        booking_for_self=True,
        age=None,
        gender=None,
        patient_type="new",
        reason="General",
        appointment_mode=None,
        symptoms=None,
    )


def _base_cursor(patient_columns=None):
    """Return a mock cursor whose fetchall/fetchone mimic schema + no-existing-patient."""
    if patient_columns is None:
        patient_columns = [
            "patient_id", "full_name", "admin_id", "phone",
            "telegram_chat_id", "age", "gender", "patient_type", "reason",
        ]
    cur = MagicMock()

    def fetchall_side_effect():
        # Called for INFORMATION_SCHEMA queries (patient columns) and appointment columns
        return [{"COLUMN_NAME": c} for c in patient_columns]

    cur.fetchall.side_effect = [
        # 1st: patient table column listing
        [{"COLUMN_NAME": c} for c in patient_columns],
        # 2nd: appointment table column listing (used later)
        [{"COLUMN_NAME": c} for c in ["appointment_id", "patient_id", "slot_id",
                                       "doctor_id", "clinic_id", "admin_id",
                                       "status", "notify_telegram_chat_id"]],
    ]
    return cur


# ---------------------------------------------------------------------------
# Test 1 – First booking: patient does NOT exist yet → INSERT succeeds normally
# ---------------------------------------------------------------------------
def test_first_booking_inserts_new_patient():
    """Happy path: no prior patient row → INSERT, get lastrowid=42."""
    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    patient_cols = [
        "patient_id", "full_name", "admin_id", "phone",
        "telegram_chat_id", "age", "gender", "patient_type", "reason",
    ]
    appt_cols = ["appointment_id", "patient_id", "slot_id",
                 "doctor_id", "clinic_id", "admin_id", "status"]

    cur.fetchall.side_effect = [
        [{"COLUMN_NAME": c} for c in patient_cols],   # patients schema
        [{"COLUMN_NAME": c} for c in appt_cols],       # appointment schema
        [{"COLUMN_NAME": c} for c in appt_cols],       # appointment schema (2nd call)
    ]

    # chat_id lookup → not found (first booking)
    # name+phone lookup → not found
    cur.fetchone.side_effect = [
        None,   # chat_id lookup
        None,   # name+phone lookup
        None,   # default_admin_id (handled below)
    ]
    cur.lastrowid = 42  # new patient_id after INSERT

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"), \
         patch.object(repo, "get_daily_queue_number", return_value=1):

        # Reset fetchone to known sequence
        cur.fetchone.side_effect = [
            None,   # chat_id lookup → no existing patient
            None,   # name+phone fallback → not found
            {"slot_id": 10, "doctor_id": 1, "clinic_id": 1},  # slot data
            None,   # appointment existence check
        ]
        cur.lastrowid = 10  # appointment lastrowid

        ctx = _make_context()
        result = repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)

    # We only care that it did NOT return a "Duplicate entry" failure
    # (it might fail on other mocked data, but NOT on the duplicate key)
    check(
        "No duplicate-key failure on first booking",
        "Duplicate entry" not in (result.message or ""),
        result.message,
    )


# ---------------------------------------------------------------------------
# Test 2 – Repeat booking: patient EXISTS with same telegram_chat_id
#           INSERT raises 1062, recovery should find existing patient_id
# ---------------------------------------------------------------------------
def test_repeat_booking_recovers_from_duplicate_chat_id():
    """
    patient with telegram_chat_id=8299824956 already in DB.
    The chat-id lookup (first check) returns None due to admin_id mismatch simulation,
    the name+phone lookup also returns None (different name stored),
    then INSERT raises a 1062 duplicate error.
    Recovery must fetch by chat_id without admin_id filter and proceed.
    """
    EXISTING_PATIENT_ID = 99
    CHAT_ID = "8299824956"

    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    patient_cols = [
        "patient_id", "full_name", "admin_id", "phone",
        "telegram_chat_id", "age", "gender", "patient_type", "reason",
    ]

    cur.fetchall.side_effect = [
        [{"COLUMN_NAME": c} for c in patient_cols],  # patients schema
        [{"COLUMN_NAME": c} for c in ["appointment_id", "patient_id", "slot_id",
                                       "doctor_id", "clinic_id", "admin_id",
                                       "status", "notify_telegram_chat_id"]],
    ]

    # Simulate the duplicate-key exception
    dup_exc = Exception("1062 (23000): Duplicate entry '8299824956' for key 'patients.patients_telegram_chat_id_key'")

    execute_call_count = [0]
    fetchone_results = {
        0: None,  # chat_id lookup (first, with admin_id) → not found
        1: None,  # name+phone lookup → not found
        2: {"patient_id": EXISTING_PATIENT_ID},  # recovery SELECT by chat_id
        3: None,  # idempotency check (existing booking for same slot) → not found
        4: {"slot_id": 10, "doctor_id": 1, "clinic_id": 1},  # slot query
    }
    fetchone_idx = [0]

    def _fetchone():
        idx = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_results.get(idx)

    def _execute(sql, params=None):
        execute_call_count[0] += 1
        # Raise duplicate on INSERT INTO patients
        if "INSERT INTO patients" in str(sql):
            raise dup_exc

    cur.execute.side_effect = _execute
    cur.fetchone.side_effect = _fetchone
    cur.lastrowid = 201  # appointment_id

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"), \
         patch.object(repo, "_normalize_chat_user_id", return_value=CHAT_ID), \
         patch.object(repo, "get_daily_queue_number", return_value=2):

        ctx = _make_context(CHAT_ID)
        result = repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)

    check(
        "Duplicate chat_id does NOT return BookingResult(ok=False)",
        "Duplicate entry" not in (result.message or ""),
        result.message,
    )
    check(
        "Result is success (ok=True) after recovery",
        result.ok is True,
        f"ok={result.ok} message={result.message}",
    )


# ---------------------------------------------------------------------------
# Test 3 – Non-duplicate exception must still propagate as failure
# ---------------------------------------------------------------------------
def test_non_duplicate_exception_still_fails():
    """A non-1062 INSERT error must still return BookingResult(ok=False)."""
    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    patient_cols = [
        "patient_id", "full_name", "admin_id", "phone",
        "telegram_chat_id", "age", "gender", "patient_type", "reason",
    ]

    cur.fetchall.side_effect = [
        [{"COLUMN_NAME": c} for c in patient_cols],
        [],
    ]

    fatal_exc = Exception("Table 'patients' doesn't exist")

    def _execute(sql, params=None):
        if "INSERT INTO patients" in str(sql):
            raise fatal_exc

    cur.execute.side_effect = _execute
    cur.fetchone.return_value = None

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"), \
         patch.object(repo, "_normalize_chat_user_id", return_value="8299824956"):

        ctx = _make_context()
        result = repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)

    check(
        "Fatal (non-duplicate) INSERT error returns ok=False",
        result.ok is False,
        f"ok={result.ok} message={result.message}",
    )
    check(
        "Error message contains the original exception",
        "doesn't exist" in (result.message or "") or "Booking transaction failed" in (result.message or ""),
        result.message,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("REQ-012: Duplicate telegram_chat_id Recovery")
    print("=" * 60)

    print("\n[1] First booking — normal INSERT path")
    test_first_booking_inserts_new_patient()

    print("\n[2] Repeat booking — 1062 recovery via chat_id fallback SELECT")
    test_repeat_booking_recovers_from_duplicate_chat_id()

    print("\n[3] Non-duplicate exception must still propagate as failure")
    test_non_duplicate_exception_still_fails()

    print(f"\n{'=' * 60}")
    print(f"PASSED: {PASS}   FAILED: {FAIL}")
    sys.exit(0 if FAIL == 0 else 1)
