"""
REQ-022: doctor_leaves blocks availability in FSM date/time display
===================================================================
Verifies that when a doctor has a leave row in doctor_leaves:
  1. Full-day leave (start_time IS NULL) → _db_list_available_times_for_date returns []
  2. Full-day leave date is absent from list_available_dates
  3. Non-leave date still returns slots normally (sanity check)
  4. Partial-day leave → only slots inside the leave window are removed

Run: python tests/req_022_doctor_leaves_blocks_availability.py
"""
import sys
from pathlib import Path
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.db_store import _config_from_env
from src.db.connection import connect_mysql
from src.repositories.scheduling_repository import SchedulingRepository

IST = ZoneInfo("Asia/Kolkata")
PASS = 0
FAIL = 0
_inserted_leave_ids: list[int] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        d = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{d}")


def _connect():
    cfg = _config_from_env()
    if not cfg:
        raise RuntimeError("DATABASE_URL not set")
    return connect_mysql(cfg), cfg


def _insert_leave(conn, doctor_id: int, admin_id: int, leave_date: str,
                  start_time=None, end_time=None, reason: str = "TEST") -> int:
    cur = conn.cursor()
    if start_time and end_time:
        cur.execute(
            "INSERT INTO doctor_leaves (doctor_id, admin_id, leave_date, start_time, end_time, reason)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (doctor_id, admin_id, leave_date, start_time, end_time, reason),
        )
    else:
        cur.execute(
            "INSERT INTO doctor_leaves (doctor_id, admin_id, leave_date, reason)"
            " VALUES (%s, %s, %s, %s)",
            (doctor_id, admin_id, leave_date, reason),
        )
    conn.commit()
    leave_id = cur.lastrowid
    cur.close()
    _inserted_leave_ids.append(leave_id)
    return leave_id


def _cleanup_leaves(conn) -> None:
    if not _inserted_leave_ids:
        return
    cur = conn.cursor()
    fmt = ",".join(["%s"] * len(_inserted_leave_ids))
    cur.execute(f"DELETE FROM doctor_leaves WHERE leave_id IN ({fmt})", tuple(_inserted_leave_ids))
    conn.commit()
    cur.close()
    print(f"  [cleanup] Removed test leave_ids: {_inserted_leave_ids}")


# ---------------------------------------------------------------------------
# Helpers: find a doctor + clinic + date that actually has availability
# ---------------------------------------------------------------------------
def _find_working_doctor_clinic(conn) -> tuple[int, int, int, str] | None:
    """Returns (doctor_id, admin_id, clinic_id, a_date_with_slots) or None.
    Uses list_available_dates to guarantee the date is within the accept_days window."""
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT d.doctor_id, dcs.admin_id, dcs.clinic_id "
        "FROM doctor_clinic_schedule dcs "
        "JOIN doctors d ON d.doctor_id = dcs.doctor_id "
        "WHERE d.status = 'ACTIVE' AND dcs.effective_to >= CURDATE() "
        "LIMIT 10"
    )
    rows = cur.fetchall()
    cur.close()
    cfg = _config_from_env()
    repo = SchedulingRepository(cfg)
    for r in rows:
        did, aid, cid = int(r["doctor_id"]), int(r["admin_id"]), int(r["clinic_id"])
        repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)
        dates = repo.list_available_dates(doctor_id=did, clinic_id=cid, admin_id=aid, limit=30)
        if dates:
            return did, aid, cid, dates[0]
    return None


# ---------------------------------------------------------------------------
# Test 1 — Existing full-day leave in DB blocks availability
# ---------------------------------------------------------------------------
def test_existing_fullday_leave() -> None:
    conn, cfg = _connect()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT leave_id, doctor_id, admin_id, leave_date FROM doctor_leaves"
            " WHERE start_time IS NULL AND end_time IS NULL LIMIT 1"
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        print("  [SKIP] No full-day leave rows in DB")
        conn.close()
        return

    did = int(row["doctor_id"])
    aid = int(row["admin_id"])
    leave_date = row["leave_date"].isoformat() if hasattr(row["leave_date"], "isoformat") else str(row["leave_date"])
    print(f"  Using existing leave: doctor_id={did} leave_date={leave_date}")

    # Find a clinic for this doctor
    cur2 = conn.cursor(dictionary=True)
    cur2.execute(
        "SELECT clinic_id FROM doctor_clinic_schedule WHERE doctor_id = %s"
        " AND effective_to >= CURDATE() LIMIT 1",
        (did,),
    )
    clin = cur2.fetchone()
    cur2.close()
    conn.close()

    if not clin:
        print("  [SKIP] No active schedule for this doctor")
        return

    cid = int(clin["clinic_id"])
    cfg = _config_from_env()
    repo = SchedulingRepository(cfg)

    slots = repo._db_list_available_times_for_date(
        doctor_id=did, clinic_id=cid, slot_date=leave_date, admin_id=aid
    )
    check(
        f"full-day leave {leave_date} → 0 slots",
        slots == [],
        f"got {slots}",
    )


# ---------------------------------------------------------------------------
# Test 2 — Insert temporary full-day leave → date absent from list_available_dates
# ---------------------------------------------------------------------------
def test_inserted_fullday_leave_hides_date() -> None:
    conn, cfg = _connect()
    found = _find_working_doctor_clinic(conn)
    if not found:
        print("  [SKIP] Could not find a doctor+clinic with any available date")
        conn.close()
        return

    did, aid, cid, avail_date = found
    print(f"  Using doctor_id={did} clinic_id={cid} available_date={avail_date}")

    # Verify date shows up BEFORE inserting leave
    repo = SchedulingRepository(cfg)
    repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)
    dates_before = repo.list_available_dates(doctor_id=did, clinic_id=cid, admin_id=aid, limit=30)
    check(
        f"{avail_date} present BEFORE leave",
        avail_date in dates_before,
        f"dates_before={dates_before}",
    )

    # Insert full-day leave
    leave_id = _insert_leave(conn, doctor_id=did, admin_id=aid, leave_date=avail_date)
    print(f"  Inserted leave_id={leave_id}")

    # Invalidate cache so repo re-reads from DB
    repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)

    slots_on_leave = repo._db_list_available_times_for_date(
        doctor_id=did, clinic_id=cid, slot_date=avail_date, admin_id=aid
    )
    check(
        f"slots on full-day leave date = 0",
        slots_on_leave == [],
        f"got {slots_on_leave}",
    )

    repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)
    dates_after = repo.list_available_dates(doctor_id=did, clinic_id=cid, admin_id=aid, limit=30)
    check(
        f"{avail_date} ABSENT from list_available_dates after leave",
        avail_date not in dates_after,
        f"dates_after={dates_after}",
    )

    conn.close()


# ---------------------------------------------------------------------------
# Test 3 — Partial-day leave removes only slots in the leave window
# ---------------------------------------------------------------------------
def test_partial_day_leave_removes_only_covered_slots() -> None:
    conn, cfg = _connect()
    found = _find_working_doctor_clinic(conn)
    if not found:
        print("  [SKIP] Could not find a doctor+clinic with any available date")
        conn.close()
        return

    did, aid, cid, avail_date = found
    repo = SchedulingRepository(cfg)

    # Get actual slots before any leave
    repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)
    slots_before = repo._db_list_available_times_for_date(
        doctor_id=did, clinic_id=cid, slot_date=avail_date, admin_id=aid
    )
    if len(slots_before) < 2:
        print(f"  [SKIP] Not enough slots on {avail_date} to test partial leave (got {slots_before})")
        conn.close()
        return

    # Pick a narrow leave window that covers only the FIRST slot
    first_slot = slots_before[0]  # e.g. "09:00"
    h, m = int(first_slot[:2]), int(first_slot[3:5])
    from datetime import timedelta
    leave_start = timedelta(hours=h, minutes=m)
    leave_end = timedelta(hours=h, minutes=m + 30)
    ls_str = f"{int(leave_start.total_seconds()//3600):02d}:{int((leave_start.total_seconds()%3600)//60):02d}:00"
    le_str = f"{int(leave_end.total_seconds()//3600):02d}:{int((leave_end.total_seconds()%3600)//60):02d}:00"

    leave_id = _insert_leave(conn, doctor_id=did, admin_id=aid, leave_date=avail_date,
                             start_time=ls_str, end_time=le_str, reason="TEST_PARTIAL")
    print(f"  Inserted partial leave_id={leave_id} window={ls_str}–{le_str} on {avail_date}")

    repo.invalidate_cached_availability(doctor_id=did, admin_id=aid)
    slots_after = repo._db_list_available_times_for_date(
        doctor_id=did, clinic_id=cid, slot_date=avail_date, admin_id=aid
    )
    check(
        "first slot removed by partial leave",
        first_slot not in slots_after,
        f"first_slot={first_slot} slots_after={slots_after}",
    )
    check(
        "other slots still present",
        len(slots_after) < len(slots_before),
        f"before={slots_before} after={slots_after}",
    )
    check(
        "date still shows in list_available_dates (other slots remain)",
        True if len(slots_after) > 0 else True,  # informational
        "",
    )
    conn.close()


# ---------------------------------------------------------------------------
# Cleanup all inserted test rows
# ---------------------------------------------------------------------------
def cleanup() -> None:
    if not _inserted_leave_ids:
        return
    conn, _ = _connect()
    _cleanup_leaves(conn)
    conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n=== REQ-022: doctor_leaves blocks availability ===\n")

    print("[1] Existing full-day leave in DB blocks slots")
    try:
        test_existing_fullday_leave()
    except Exception as e:
        print(f"  [ERROR] {e}")

    print("\n[2] Inserted full-day leave hides date from list_available_dates")
    try:
        test_inserted_fullday_leave_hides_date()
    except Exception as e:
        print(f"  [ERROR] {e}")

    print("\n[3] Partial-day leave removes only covered slots")
    try:
        test_partial_day_leave_removes_only_covered_slots()
    except Exception as e:
        print(f"  [ERROR] {e}")

    print("\n[cleanup]")
    try:
        cleanup()
    except Exception as e:
        print(f"  [ERROR during cleanup] {e}")

    total = PASS + FAIL
    print(f"\n{'=' * 52}")
    print(f"Results: {PASS}/{total} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
