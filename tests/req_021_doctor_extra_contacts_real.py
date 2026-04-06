"""
REQ-021: Doctor extra contacts from doctor_whatsapp_numbers
============================================================
Verifies that:
  1. get_extra_doctor_contacts() reads real rows from doctor_whatsapp_numbers table.
  2. Deduplication works — same (whatsapp, telegram) pair returned only once.
  3. The scheduler merges primary + extra contacts, deduplicates, and does not
     send to the source whatsapp number (self-send guard).
  4. doctor_id with no extra rows returns empty list.

Uses REAL DB for parts 1-3 and mocked scheduler for parts 4-5.
Run: python tests/req_021_doctor_extra_contacts_real.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.db_store import _config_from_env
from src.db.connection import connect_mysql
from src.repositories.booking_repository import BookingRepository, DoctorReminder
from src.automation.scheduler import AutomationScheduler

IST = ZoneInfo("Asia/Kolkata")
PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        d = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{d}")


def make_real_repo() -> BookingRepository:
    cfg = _config_from_env()
    if not cfg:
        raise RuntimeError("DATABASE_URL not set — cannot run real DB test")
    return BookingRepository(cfg)


# ---------------------------------------------------------------------------
# Test 1 — Real DB: get_extra_doctor_contacts returns actual rows
# ---------------------------------------------------------------------------
def test_real_db_extra_contacts() -> None:
    """Connect to live DB, query doctor_whatsapp_numbers, verify structure."""
    print("  Connecting to live DB ...")
    repo = make_real_repo()
    cfg = _config_from_env()
    conn = connect_mysql(cfg)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT DISTINCT doctor_id FROM doctor_whatsapp_numbers LIMIT 5")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not rows:
        print("  [SKIP] doctor_whatsapp_numbers is empty")
        return

    doctor_ids = [int(r["doctor_id"]) for r in rows]
    print(f"  Found doctor_ids with extra contacts: {doctor_ids}")

    result = repo.get_extra_doctor_contacts(doctor_ids)

    check("returns dict", isinstance(result, dict))
    check(
        "all queried doctor_ids present",
        all(did in result for did in doctor_ids),
        f"missing={[d for d in doctor_ids if d not in result]}",
    )
    for did, contacts in result.items():
        check(f"doctor_id={did} has >= 1 contact", len(contacts) >= 1, f"count={len(contacts)}")
        for c in contacts:
            check(
                f"doctor_id={did} contact has whatsapp+telegram keys",
                "whatsapp" in c and "telegram" in c,
                f"keys={list(c.keys())}",
            )
            check(
                f"doctor_id={did} at least one value non-empty",
                bool(c.get("whatsapp")) or bool(c.get("telegram")),
                f"contact={c}",
            )


# ---------------------------------------------------------------------------
# Test 2 — Real DB: empty result for doctor_id that has no extra contacts
# ---------------------------------------------------------------------------
def test_real_db_no_extra_for_unknown_doctor() -> None:
    repo = make_real_repo()
    result = repo.get_extra_doctor_contacts([999999])
    check(
        "unknown doctor_id returns empty",
        len(result.get(999999, [])) == 0,
        f"result={result}",
    )


# ---------------------------------------------------------------------------
# Test 3 — Real DB: no duplicate pairs for same doctor
# ---------------------------------------------------------------------------
def test_real_db_dedup() -> None:
    repo = make_real_repo()
    cfg = _config_from_env()
    conn = connect_mysql(cfg)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT DISTINCT doctor_id FROM doctor_whatsapp_numbers LIMIT 1")
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        print("  [SKIP] no rows in doctor_whatsapp_numbers")
        return

    did = int(row["doctor_id"])
    contacts = repo.get_extra_doctor_contacts([did]).get(did, [])
    pairs = [(c["whatsapp"], c["telegram"]) for c in contacts]
    check(
        f"no duplicate (whatsapp,telegram) pairs for doctor_id={did}",
        len(pairs) == len(set(pairs)),
        f"pairs={pairs}",
    )


# ---------------------------------------------------------------------------
# Helper: make a DoctorReminder row for scheduler tests
# The scheduler window check: center_seconds - window_seconds <= delta <= center + window_seconds
# With lead_minutes=10 and window_seconds=9999, we want delta in [-9399, 10599].
# Set schedule_start_time = now + 10 min → delta ≈ 600, safely inside the window.
# ---------------------------------------------------------------------------
def _make_reminder(
    doctor_id: int,
    doctor_whatsapp: str,
    doctor_telegram: str,
    schedule_id: int,
    appointment_id: int,
    lead_minutes: int = 10,
) -> DoctorReminder:
    from datetime import timedelta
    now = datetime.now(IST)
    window_center = now + timedelta(minutes=lead_minutes)
    slot_date = window_center.strftime("%Y-%m-%d")
    start_time = window_center.strftime("%H:%M")
    end_time = (window_center + timedelta(hours=2)).strftime("%H:%M")
    return DoctorReminder(
        appointment_id=appointment_id,
        doctor_id=doctor_id,
        doctor_whatsapp=doctor_whatsapp,
        doctor_telegram_chat_id=doctor_telegram,
        patient_name="Test Patient",
        patient_contact="+910000000000",
        clinic_name="Test Clinic",
        slot_date=slot_date,
        slot_time=start_time,
        status="BOOKED",
        booking_number=1000 + appointment_id,
        schedule_id=schedule_id,
        schedule_start_time=start_time,
        schedule_end_time=end_time,
    )


# ---------------------------------------------------------------------------
# Test 4 — Scheduler merges primary + extra contacts, deduplicates
# ---------------------------------------------------------------------------
def test_scheduler_merges_extra_contacts() -> None:
    """Primary contact + extra contacts are merged; duplicates appear only once."""
    primary_wa = "+919999999999"
    primary_tg = "7001234567"

    extra_contacts_map = {
        1: [
            {"whatsapp": "+919999999999", "telegram": ""},        # duplicate of primary → must dedup
            {"whatsapp": "+918888888888", "telegram": ""},        # new WA
            {"whatsapp": "",             "telegram": "888100200300"},  # extra Telegram only
        ]
    }

    reminder_row = _make_reminder(
        doctor_id=1,
        doctor_whatsapp=primary_wa,
        doctor_telegram=primary_tg,
        schedule_id=5,
        appointment_id=1,
    )

    mock_repo = MagicMock()
    mock_repo.list_due_doctor_reminders.return_value = [reminder_row]
    mock_repo.get_extra_doctor_contacts.return_value = extra_contacts_map
    mock_repo.is_reminder_sent.return_value = False
    mock_repo.insert_or_get_reminder_queue.return_value = 99

    sent_to: list[str] = []

    scheduler = AutomationScheduler(
        booking_repository=mock_repo,
        send_message_fn=lambda to, body: None,
        send_document_fn=lambda to, fp, cap: sent_to.append(to),
        source_whatsapp_number="",
        enabled=True,
        doctor_reminder_enabled=True,
        doctor_reminder_interval_seconds=60,
        doctor_reminder_lead_minutes=10,
        doctor_reminder_lead_minutes_list=[10],
        doctor_reminder_window_seconds=9999,
    )

    with patch.object(scheduler, "_build_doctor_report_xlsx", return_value="fake.xlsx"), \
         patch.object(scheduler._reminder_keys, "has", return_value=False):
        scheduler._run_reminders_once()

    check(
        "get_extra_doctor_contacts called once",
        mock_repo.get_extra_doctor_contacts.call_count == 1,
        f"calls={mock_repo.get_extra_doctor_contacts.call_count}",
    )
    expected = {
        "whatsapp:+919999999999",
        "telegram:7001234567",
        "whatsapp:+918888888888",
        "telegram:888100200300",
    }
    actual = set(sent_to)
    check(
        "sent to all 4 unique destinations",
        actual == expected,
        f"expected={expected}  actual={actual}",
    )
    check(
        "no duplicate sends",
        len(sent_to) == len(set(sent_to)),
        f"sent_to={sent_to}",
    )


# ---------------------------------------------------------------------------
# Test 5 — Source WA guard applies to extra contacts too
# ---------------------------------------------------------------------------
def test_scheduler_source_whatsapp_guard() -> None:
    """An extra contact that equals the source WA number must not be sent to."""
    source_wa = "+919111111111"

    extra_contacts_map = {
        1: [
            {"whatsapp": "+919111111111", "telegram": ""},  # matches source → excluded
            {"whatsapp": "+918222222222", "telegram": ""},  # different → included
        ]
    }

    reminder_row = _make_reminder(
        doctor_id=1,
        doctor_whatsapp="",
        doctor_telegram="",
        schedule_id=6,
        appointment_id=2,
    )

    mock_repo = MagicMock()
    mock_repo.list_due_doctor_reminders.return_value = [reminder_row]
    mock_repo.get_extra_doctor_contacts.return_value = extra_contacts_map
    mock_repo.is_reminder_sent.return_value = False
    mock_repo.insert_or_get_reminder_queue.return_value = 100

    sent_to: list[str] = []

    scheduler = AutomationScheduler(
        booking_repository=mock_repo,
        send_message_fn=lambda to, body: None,
        send_document_fn=lambda to, fp, cap: sent_to.append(to),
        source_whatsapp_number=source_wa,
        enabled=True,
        doctor_reminder_enabled=True,
        doctor_reminder_interval_seconds=60,
        doctor_reminder_lead_minutes=10,
        doctor_reminder_lead_minutes_list=[10],
        doctor_reminder_window_seconds=9999,
    )

    with patch.object(scheduler, "_build_doctor_report_xlsx", return_value="fake.xlsx"), \
         patch.object(scheduler._reminder_keys, "has", return_value=False):
        scheduler._run_reminders_once()

    check(
        "source WA not in sent_to",
        "whatsapp:+919111111111" not in sent_to,
        f"sent_to={sent_to}",
    )
    check(
        "other extra WA IS in sent_to",
        "whatsapp:+918222222222" in sent_to,
        f"sent_to={sent_to}",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n=== REQ-021: Doctor Extra Contacts (doctor_whatsapp_numbers) ===\n")

    print("[1] Real DB: get_extra_doctor_contacts returns actual rows")
    test_real_db_extra_contacts()

    print("\n[2] Real DB: unknown doctor_id returns empty")
    test_real_db_no_extra_for_unknown_doctor()

    print("\n[3] Real DB: no duplicate pairs")
    test_real_db_dedup()

    print("\n[4] Scheduler merges primary + extra, deduplicates")
    test_scheduler_merges_extra_contacts()

    print("\n[5] Scheduler source WA guard applies to extra contacts")
    test_scheduler_source_whatsapp_guard()

    total = PASS + FAIL
    print(f"\n{'=' * 52}")
    print(f"Results: {PASS}/{total} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
