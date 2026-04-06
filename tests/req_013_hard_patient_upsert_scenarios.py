"""
REQ-013: Hard Patient Upsert & Past-Slot Filter Scenarios
Tests the three implemented changes under adversarial conditions:

  A) Past-slot filtering: BOOKED rows with past date/time are ignored by
     list_active_appointments_by_chat_user_id and list_active_appointments_by_phone_number.

  B) Duplicate recovery — same admin: 1062 during INSERT recovers via
     same-admin chat_id SELECT, re-applies patient field updates, proceeds to booking.

  C) Duplicate recovery — cross-admin conflict: 1062 fires, same-admin lookup
     misses, global lookup finds a row owned by a different admin → returns
     clear conflict failure, does NOT book.

  D) Duplicate recovery — phone fallback: 1062 fires, chat lookup misses
     entirely (no chat_column), phone fallback with admin_id finds the patient
     and update is applied.

Run: python tests/req_013_hard_patient_upsert_scenarios.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        print(f"  [FAIL] {label}{(' -- ' + detail) if detail else ''}")


def _make_context(chat_id="8299824956", phone="9029298287", name="Vijjejee"):
    return SimpleNamespace(
        patient_name=name,
        appointment_date="2026-03-01",
        appointment_time="10:00",
        clinic_id=1,
        phone_number=phone,
        chat_user_id=chat_id,
        booking_for_self=True,
        age=None,
        gender="Male",
        patient_type="new",
        reason="General",
        appointment_mode=None,
        symptoms=None,
    )


def _base_patient_cols():
    return [
        "patient_id", "full_name", "admin_id", "phone",
        "telegram_chat_id", "age", "gender", "patient_type", "reason",
    ]


def _base_appt_cols():
    return ["appointment_id", "patient_id", "slot_id", "doctor_id",
            "clinic_id", "admin_id", "status", "notify_telegram_chat_id"]


# ─────────────────────────────────────────────────────────────
# A) Past-slot filtering
# ─────────────────────────────────────────────────────────────
def test_past_slot_ignored_by_chat_lookup():
    """list_active_appointments_by_chat_user_id must skip a BOOKED row whose
    slot_date is in the past (2020-01-01) and return empty list."""
    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    patient_col_rows = [{"COLUMN_NAME": c} for c in _base_patient_cols()]
    past_slot_row = {
        "appointment_id": 1,
        "clinic_id": 1,
        "doctor_id": 1,
        "booking_number": 1,
        "clinic_name": "City Care",
        "slot_date": "2020-01-01",   # ← well in the past
        "slot_time": "10:00",
        "chat_user_value": "8299824956",
    }

    cur.fetchall.side_effect = [
        patient_col_rows,   # INFORMATION_SCHEMA patients schema
        [past_slot_row],    # appointment rows
    ]

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_chat_user_id", return_value="8299824956"):

        result = repo.list_active_appointments_by_chat_user_id("8299824956", admin_id=1)

    check("Past-dated BOOKED row ignored — returns empty list", result == [], str(result))


def test_future_slot_returned_by_chat_lookup():
    """list_active_appointments_by_chat_user_id must return a BOOKED row
    whose slot_date is in the future."""
    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    patient_col_rows = [{"COLUMN_NAME": c} for c in _base_patient_cols()]
    future_row = {
        "appointment_id": 2,
        "clinic_id": 1,
        "doctor_id": 1,
        "booking_number": 1,
        "clinic_name": "City Care",
        "slot_date": "2099-12-31",  # ← far future
        "slot_time": "10:00",
        "chat_user_value": "8299824956",
    }

    cur.fetchall.side_effect = [
        patient_col_rows,
        [future_row],
    ]

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_chat_user_id", return_value="8299824956"):

        result = repo.list_active_appointments_by_chat_user_id("8299824956", admin_id=1)

    check("Future BOOKED row returned (not filtered)", len(result) == 1, str(result))


def test_past_phone_slot_ignored():
    """list_active_appointments_by_phone_number must skip past-dated rows."""
    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    past_row = {
        "appointment_id": 5,
        "clinic_id": 1,
        "doctor_id": 1,
        "booking_number": 1,
        "clinic_name": "HC",
        "slot_date": "2019-06-15",
        "slot_time": "09:00",
        "patient_phone": "9029298287",
    }

    cur.fetchall.return_value = [past_row]

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=1), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"):

        result = repo.list_active_appointments_by_phone_number("9029298287", admin_id=1)

    check("Past-dated phone row ignored — returns empty list", result == [], str(result))


# ─────────────────────────────────────────────────────────────
# B) Duplicate recovery — same admin, fields updated
# ─────────────────────────────────────────────────────────────
def test_duplicate_same_admin_recovery_updates_fields():
    """
    1062 fires → same-admin recovery finds patient_id=99 →
    _update_patient_from_values must be called on patient_id=99 →
    booking proceeds (ok=True).
    """
    EXISTING_PID = 99
    CHAT_ID = "8299824956"
    ADMIN_ID = 1

    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    dup_exc = Exception("1062 (23000): Duplicate entry '8299824956' for key 'patients.patients_telegram_chat_id_key'")

    patient_col_rows = [{"COLUMN_NAME": c} for c in _base_patient_cols()]
    appt_col_rows = [{"COLUMN_NAME": c} for c in _base_appt_cols()]

    cur.fetchall.side_effect = [
        patient_col_rows,   # patients schema
        appt_col_rows,      # appointment table schema
    ]

    fetchone_seq = [
        None,                                       # 0: pre-check chat_id lookup → miss
        None,                                       # 1: name+phone fallback → miss
        {"patient_id": EXISTING_PID, "admin_id": ADMIN_ID},  # 2: recovery same-admin lookup ✓
        None,                                       # 3: idempotency check → no existing booking
        {"slot_id": 10, "doctor_id": 1, "clinic_id": 1},     # 4: slot fetch
    ]
    fetchone_idx = [0]

    def _fetchone():
        i = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_seq[i] if i < len(fetchone_seq) else None

    update_calls = []

    def _execute(sql, params=None):
        sql_s = str(sql)
        if "INSERT INTO patients" in sql_s:
            raise dup_exc
        if "UPDATE patients" in sql_s and "SET phone" in sql_s:
            update_calls.append(params)

    cur.execute.side_effect = _execute
    cur.fetchone.side_effect = _fetchone
    cur.lastrowid = 201

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=ADMIN_ID), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"), \
         patch.object(repo, "_normalize_chat_user_id", return_value=CHAT_ID), \
         patch.object(repo, "get_daily_queue_number", return_value=2):

        ctx = _make_context(CHAT_ID)
        result = repo.save_confirmed_appointment(ctx, admin_id=ADMIN_ID, doctor_id=1)

    check("Same-admin recovery: booking ok=True", result.ok is True, f"ok={result.ok} msg={result.message}")
    check(
        "Same-admin recovery: UPDATE patients called after recovery",
        len(update_calls) > 0,
        f"UPDATE calls={update_calls}",
    )


# ─────────────────────────────────────────────────────────────
# C) Duplicate recovery — cross-admin conflict
# ─────────────────────────────────────────────────────────────
def test_duplicate_cross_admin_returns_conflict_failure():
    """
    1062 fires → same-admin lookup misses → global lookup finds patient
    owned by admin_id=2 (different from actual_admin_id=1) →
    must return ok=False with conflict message, NOT proceed to booking.
    """
    CHAT_ID = "8299824956"
    MY_ADMIN = 1
    OTHER_ADMIN = 2

    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    dup_exc = Exception("1062 (23000): Duplicate entry '8299824956' for key 'patients.patients_telegram_chat_id_key'")

    patient_col_rows = [{"COLUMN_NAME": c} for c in _base_patient_cols()]
    appt_col_rows = [{"COLUMN_NAME": c} for c in _base_appt_cols()]

    cur.fetchall.side_effect = [
        patient_col_rows,
        appt_col_rows,
    ]

    fetchone_seq = [
        None,                                                    # 0: pre-check chat_id (admin-scoped) → miss
        None,                                                    # 1: name+phone fallback → miss
        None,                                                    # 2: recovery same-admin lookup → miss
        {"patient_id": 55, "admin_id": OTHER_ADMIN},            # 3: global lookup → finds OTHER admin's row
    ]
    fetchone_idx = [0]

    def _fetchone():
        i = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_seq[i] if i < len(fetchone_seq) else None

    def _execute(sql, params=None):
        if "INSERT INTO patients" in str(sql):
            raise dup_exc

    cur.execute.side_effect = _execute
    cur.fetchone.side_effect = _fetchone

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=MY_ADMIN), \
         patch.object(repo, "_normalize_phone", return_value=""), \
         patch.object(repo, "_normalize_chat_user_id", return_value=CHAT_ID):

        ctx = _make_context(CHAT_ID)
        result = repo.save_confirmed_appointment(ctx, admin_id=MY_ADMIN, doctor_id=1)

    check("Cross-admin conflict: ok=False", result.ok is False, f"ok={result.ok} msg={result.message}")
    check(
        "Cross-admin conflict: message contains admin profile indication",
        "admin" in (result.message or "").lower(),
        result.message,
    )


# ─────────────────────────────────────────────────────────────
# D) Duplicate recovery — phone fallback (no chat column)
# ─────────────────────────────────────────────────────────────
def test_duplicate_phone_fallback_no_chat_column():
    """
    patients table has no telegram_chat_id column.
    1062 fires (duplicate phone) → chat recovery skipped →
    phone fallback (admin-scoped) finds patient_id=77 → booking proceeds.
    """
    NO_CHAT_COLS = [
        "patient_id", "full_name", "admin_id", "phone",
        "age", "gender", "patient_type", "reason",
        # NOTE: no telegram_chat_id
    ]
    ADMIN_ID = 1

    repo = BookingRepository.__new__(BookingRepository)
    repo._config = MagicMock()

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    dup_exc = Exception("1062 (23000): Duplicate entry '9029298287' for key 'patients.patients_phone_key'")

    patient_col_rows = [{"COLUMN_NAME": c} for c in NO_CHAT_COLS]
    appt_col_rows = [{"COLUMN_NAME": c} for c in _base_appt_cols()]

    cur.fetchall.side_effect = [
        patient_col_rows,
        appt_col_rows,
    ]

    fetchone_seq = [
        None,                            # 0: name+phone fallback → miss (no pre-check chat lookup since no chat col)
        {"patient_id": 77},              # 1: phone recovery (admin-scoped) ✓
        None,                            # 2: idempotency check → no existing booking
        {"slot_id": 5, "doctor_id": 1, "clinic_id": 1},  # 3: slot fetch
    ]
    fetchone_idx = [0]

    def _fetchone():
        i = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_seq[i] if i < len(fetchone_seq) else None

    def _execute(sql, params=None):
        if "INSERT INTO patients" in str(sql):
            raise dup_exc

    cur.execute.side_effect = _execute
    cur.fetchone.side_effect = _fetchone
    cur.lastrowid = 300

    with patch.object(repo, "_connect", return_value=conn), \
         patch.object(repo, "_table_exists", return_value=True), \
         patch.object(repo, "_use_appointment_mode", return_value=False), \
         patch.object(repo, "default_admin_id", return_value=ADMIN_ID), \
         patch.object(repo, "_normalize_phone", return_value="9029298287"), \
         patch.object(repo, "_normalize_chat_user_id", return_value=""), \
         patch.object(repo, "get_daily_queue_number", return_value=1):

        ctx = _make_context(chat_id="", phone="9029298287")
        result = repo.save_confirmed_appointment(ctx, admin_id=ADMIN_ID, doctor_id=1)

    check("Phone fallback recovery: ok=True", result.ok is True, f"ok={result.ok} msg={result.message}")


# ─────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("REQ-013: Hard Patient Upsert & Past-Slot Filter")
    print("=" * 60)

    print("\n[A1] Past-dated BOOKED row ignored by chat lookup")
    test_past_slot_ignored_by_chat_lookup()

    print("\n[A2] Future BOOKED row returned by chat lookup")
    test_future_slot_returned_by_chat_lookup()

    print("\n[A3] Past-dated BOOKED row ignored by phone lookup")
    test_past_phone_slot_ignored()

    print("\n[B] Duplicate recovery — same admin, fields updated after recovery")
    test_duplicate_same_admin_recovery_updates_fields()

    print("\n[C] Duplicate recovery — cross-admin conflict returns clear failure")
    test_duplicate_cross_admin_returns_conflict_failure()

    print("\n[D] Duplicate recovery — phone fallback (no chat column) succeeds")
    test_duplicate_phone_fallback_no_chat_column()

    print(f"\n{'=' * 60}")
    print(f"PASSED: {PASS}   FAILED: {FAIL}")
    sys.exit(0 if FAIL == 0 else 1)
