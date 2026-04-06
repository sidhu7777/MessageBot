"""
INTEGRATION TEST — Dr. Sanjay T-10 Real Send
=============================================
This is a REAL integration test. It:
  1. Connects to the live MySQL DB (from .env)
  2. Queries Dr. Sanjay's (doctor_id=1) clinic + schedule for today
  3. Inserts 48 test appointments: 13:20–17:15 in 5-min slots
  4. Injects T-10 trigger at 13:10 (T-10 before 13:20)
  5. Runs the scheduler with the REAL Telegram send function
  6. Verifies the xlsx document was actually sent to telegram:8299824956
  7. Cleans up all inserted test records

Run: python tests/integration_dr_sanjay_t10_real.py

Requirements:
  - .env must have DATABASE_URL and TELEGRAM_BOT_TOKEN set
  - Dr. Sanjay (doctor_id=1) must have telegram_chat_id = 8299824956 in doctors table
  - A doctor_clinic_schedule must exist for doctor_id=1 covering today
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env FIRST before any imports that read env vars
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.db.connection import MySQLConfig, parse_mysql_url
from src.db_store import BookingRepository
from src.repositories.booking_repository import DoctorReminder
from src.automation.scheduler import AutomationScheduler

PASS = 0
FAIL = 0
INSERTED_APPOINTMENT_IDS: list[int] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{detail_str}")


def get_db_config() -> MySQLConfig:
    url = os.getenv("DATABASE_URL", "").strip()
    assert url.startswith("mysql+mysqlconnector://"), \
        "DATABASE_URL not set or invalid in .env"
    return parse_mysql_url(url)


def db_connect(config: MySQLConfig):
    """Open a direct (non-pooled) connection. Aiven needs ssl_disabled=False."""
    import mysql.connector
    return mysql.connector.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        ssl_disabled=False,
    )


# ─── Step 0: Verify DB + environment ─────────────────────────────────────────

def step_verify_env():
    print("\n[STEP 0] Verify environment")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    check("TELEGRAM_BOT_TOKEN present in .env", bool(token), "set TELEGRAM_BOT_TOKEN in .env")
    db_url = os.getenv("DATABASE_URL", "").strip()
    check("DATABASE_URL present in .env", db_url.startswith("mysql+mysqlconnector://"),
          "set DATABASE_URL in .env")
    return bool(token) and db_url.startswith("mysql+mysqlconnector://")


# ─── Step 1: Query Dr. Sanjay (doctor_id=1) from DB ──────────────────────────

def step_query_doctor(config: MySQLConfig) -> dict:
    print("\n[STEP 1] Query Dr. Sanjay (doctor_id=1) from DB")
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)

    # Get column names in doctors table
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doctors'")
    doctor_cols = {r["COLUMN_NAME"].lower() for r in cur.fetchall()}

    wa_col  = "whatsapp_number" if "whatsapp_number" in doctor_cols else None
    tg_col  = next((c for c in ("telegram_chat_id","telegram_user_id","telegram_id","chat_id")
                    if c in doctor_cols), None)

    wa_sel  = f"d.{wa_col}"  if wa_col  else "NULL"
    tg_sel  = f"d.{tg_col}"  if tg_col  else "NULL"

    # Build a safe name expression using only columns that exist
    name_candidates = [c for c in ("full_name", "doctor_name", "name", "username") if c in doctor_cols]
    name_sel = f"d.{name_candidates[0]}" if name_candidates else "'Dr. Sanjay'"

    cur.execute(f"""
        SELECT d.doctor_id,
               {name_sel} AS doctor_name,
               {wa_sel} AS whatsapp_number,
               {tg_sel} AS telegram_chat_id
        FROM doctors d
        WHERE d.doctor_id = 1
        LIMIT 1
    """)
    doc = cur.fetchone()
    cur.close()
    conn.close()

    assert doc, "doctor_id=1 not found in doctors table"
    check("doctor_id=1 found", True)
    check("telegram_chat_id populated",
          bool(doc.get("telegram_chat_id")),
          f"got {doc.get('telegram_chat_id')!r} — set {tg_col or 'telegram col missing'} in doctors table")
    print(f"    doctor_name       : {doc['doctor_name']}")
    print(f"    telegram_chat_id  : {doc['telegram_chat_id']}")
    print(f"    whatsapp_number   : {doc['whatsapp_number']}")
    return doc


# ─── Step 2: Find today's schedule for doctor_id=1 ───────────────────────────

def step_find_schedule(config: MySQLConfig) -> dict:
    print("\n[STEP 2] Find doctor_clinic_schedule for doctor_id=1 today")
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)
    today = datetime.now().date()
    # MySQL WEEKDAY: 0=Mon…6=Sun; MOD(WEEKDAY+1,7) converts to 0=Sun,1=Mon…6=Sat
    dow = (today.weekday() + 1) % 7   # same formula as in the SQL

    cur.execute("""
        SELECT dcs.schedule_id, dcs.clinic_id, dcs.start_time, dcs.end_time,
               dcs.slot_duration, COALESCE(c.clinic_name,'City Care') AS clinic_name
        FROM doctor_clinic_schedule dcs
        LEFT JOIN clinics c ON c.clinic_id = dcs.clinic_id
        WHERE dcs.doctor_id = 1
          AND dcs.day_of_week = %s
          AND dcs.effective_from <= %s
          AND dcs.effective_to   >= %s
        LIMIT 1
    """, (dow, today, today))
    sched = cur.fetchone()
    cur.close()
    conn.close()

    if not sched:
        print(f"  [WARN] No doctor_clinic_schedule found for doctor_id=1 on day_of_week={dow} (today={today}, {today.strftime('%A')})")
        print("         Please add a schedule in the DB for today's weekday and re-run.")
        check("schedule found for today", False,
              f"no doctor_clinic_schedule row for doctor_id=1 day_of_week={dow}")
        return {}

    check("schedule found", True)
    print(f"    schedule_id    : {sched['schedule_id']}")
    print(f"    clinic_id      : {sched['clinic_id']}")
    print(f"    clinic_name    : {sched['clinic_name']}")
    print(f"    window         : {sched['start_time']} – {sched['end_time']}")
    print(f"    slot_duration  : {sched['slot_duration']} min")
    return sched


# ─── Step 3: Insert 48 fake appointments 13:20-17:15, 5-min slots ────────────

def step_insert_appointments(config: MySQLConfig, sched: dict, doctor_row: dict) -> list[int]:
    print("\n[STEP 3] Insert 48 test appointments (13:20-17:15, 5-min slots)")
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)
    today = datetime.now().date()

    # Detect appointment table name
    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('appointment','appointments')")
    tables = [r["TABLE_NAME"] for r in cur.fetchall()]
    appt_table = "appointment" if "appointment" in tables else "appointments"

    # Detect appointment table columns
    cur.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='{appt_table}'")
    appt_cols = {r["COLUMN_NAME"].lower() for r in cur.fetchall()}

    # Build 48 slots: 13:20 to 17:15 in 5-min steps
    slots = []
    h, m = 13, 20
    while (h, m) < (17, 20):
        slots.append(f"{h:02d}:{m:02d}:00")
        m += 5
        if m >= 60:
            m -= 60
            h += 1
    assert len(slots) == 48

    # Check if patients table exists and grab a patient_id to attach
    patient_id = None
    cur.execute("SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='patients'")
    has_patients = cur.fetchone()["cnt"] > 0

    if has_patients:
        cur.execute("SELECT patient_id FROM patients LIMIT 1")
        row = cur.fetchone()
        patient_id = row["patient_id"] if row else None

    # Remove any leftover appointments from previous runs for doctor_id=1 today
    # in the 13:20–17:15 window so we never hit the unique-key duplicate error.
    slot_placeholders = ", ".join(["%s"] * len(slots))
    cur.execute(
        f"DELETE FROM {appt_table} "
        f"WHERE doctor_id=1 AND appointment_date=%s AND start_time IN ({slot_placeholders})",
        [str(today)] + slots,
    )
    conn.commit()
    print(f"    pre-cleanup: removed {cur.rowcount} stale test row(s)")

    # Build INSERT columns/values based on what the table actually has
    test_marker = f"TEST_{uuid.uuid4().hex[:8]}"
    inserted_ids = []

    for slot_time in slots:
        cols = {"doctor_id": 1,
                "clinic_id": sched["clinic_id"],
                "appointment_date": str(today),
                "start_time": slot_time,
                "status": "BOOKED"}
        if "schedule_id" in appt_cols:
            cols["schedule_id"] = sched["schedule_id"]
        if patient_id and "patient_id" in appt_cols:
            cols["patient_id"] = patient_id
        # Store test marker in notes/remarks if column exists
        for note_col in ("notes", "remarks", "comments", "reason"):
            if note_col in appt_cols:
                cols[note_col] = test_marker
                break
        if "admin_id" in appt_cols:
            cols["admin_id"] = 1

        col_names = ", ".join(cols.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cur.execute(
            f"INSERT INTO {appt_table} ({col_names}) VALUES ({placeholders})",
            list(cols.values()),
        )
        inserted_ids.append(cur.lastrowid)

    conn.commit()
    cur.close()
    conn.close()

    check(f"inserted {len(inserted_ids)} appointments into DB",
          len(inserted_ids) == 48, f"got {len(inserted_ids)}")
    print(f"    table          : {appt_table}")
    print(f"    appointment_ids: {inserted_ids[0]}…{inserted_ids[-1]}")
    print(f"    test_marker    : {test_marker}")
    return inserted_ids


# ─── Step 4: Run real scheduler with T-10 triggered by injected rows ────────

def step_run_scheduler_t10(
    config: MySQLConfig,
    sched: dict,
    doctor_row: dict,
    inserted_ids: list[int],
) -> list[dict]:
    """Run the real scheduler but bypass the DB time-window filter.

    The DB query (`list_due_doctor_reminders`) uses UTC_TIMESTAMP() which
    cannot be frozen from Python.  Instead we:
      1. Read the inserted appointments directly.
      2. Build DoctorReminder rows with schedule_start_time = now + 10 min.
      3. Patch list_due_doctor_reminders to return those rows.
      4. Keep the Python clock at real_now so delta = 600 s → T-10 fires.
    Everything else (xlsx build, Telegram send) is 100% real.
    """
    import urllib.request
    import urllib.error

    print("\n[STEP 4] Run real scheduler (T-10 via injected DoctorReminder rows)")

    now = datetime.now()
    fake_schedule_start = (now + timedelta(minutes=10)).strftime("%H:%M")
    today_str = now.strftime("%Y-%m-%d")

    # ── 4a. Read the appointments we just inserted directly from DB ──────────
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)
    id_ph = ", ".join(["%s"] * len(inserted_ids))
    cur.execute(
        f"SELECT appointment_id, TIME_FORMAT(start_time,'%%H:%%i') AS slot_time "
        f"FROM appointment WHERE appointment_id IN ({id_ph})",
        inserted_ids,
    )
    appt_rows = cur.fetchall()
    cur.close()
    conn.close()

    tg_id  = str(doctor_row.get("telegram_chat_id") or "").strip()
    wa_num = str(doctor_row.get("whatsapp_number") or "").strip()
    clinic = sched.get("clinic_name", "Health Plus Clinic")
    sid    = int(sched.get("schedule_id", 0))
    end_t  = str(sched.get("end_time", "17:00"))

    due_rows: list[DoctorReminder] = [
        DoctorReminder(
            appointment_id=r["appointment_id"],
            doctor_whatsapp=wa_num,
            doctor_telegram_chat_id=tg_id,
            patient_name="Test Patient",
            patient_contact="",
            clinic_name=clinic,
            slot_date=today_str,
            slot_time=str(r["slot_time"]),
            status="BOOKED",
            booking_number=None,
            schedule_id=sid,
            schedule_start_time=fake_schedule_start,
            schedule_end_time=end_t,
        )
        for r in appt_rows
    ]

    print(f"    injected rows     : {len(due_rows)} appointments")
    print(f"    fake_schedule_start: {fake_schedule_start}  (now + 10 min)")
    print(f"    real now           : {now.strftime('%H:%M:%S')}")
    print(f"    expected delta     : 600 s  (T-10 fires at 480–720 s window)")

    # ── 4b. Build the real scheduler + Telegram sender ───────────────────────
    booking_repo = BookingRepository(config)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    os.makedirs(os.path.join(str(ROOT), "data", "reports"), exist_ok=True)
    sent_docs: list[dict] = []

    def send_telegram_document(to_number: str, file_path: str, caption: str = "") -> None:
        """Actually posts the xlsx to Telegram."""
        import uuid as _uuid
        chat_id = to_number.replace("telegram:", "").strip()
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        boundary = f"----TestBoundary{_uuid.uuid4().hex}"
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            file_bytes = fh.read()
        body_parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}".encode()
        ]
        if caption:
            body_parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}".encode()
            )
        body_parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
             f"filename=\"{file_name}\"\r\nContent-Type: application/vnd.openxmlformats-officedocument"
             f".spreadsheetml.sheet\r\n\r\n").encode() + file_bytes
        )
        body_parts.append(f"--{boundary}--".encode())
        raw_body = b"\r\n".join(body_parts)
        req = urllib.request.Request(
            url=url, data=raw_body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        print(f"    Telegram API: ok={result.get('ok')} "
              f"message_id={result.get('result', {}).get('message_id')}")
        sent_docs.append({"to": to_number, "path": file_path, "caption": caption})

    def send_doc_fn(to_number: str, file_path: str, caption: str = "") -> None:
        if to_number.startswith("telegram:"):
            send_telegram_document(to_number, file_path, caption)
        else:
            print(f"    [WhatsApp] Would send {os.path.basename(file_path)} to {to_number}")
            sent_docs.append({"to": to_number, "path": file_path, "caption": caption})

    scheduler = AutomationScheduler(
        booking_repository=booking_repo,
        send_message_fn=lambda *a: None,
        send_document_fn=send_doc_fn,
        enabled=True,
        doctor_reminder_enabled=True,
        doctor_reminder_lead_minutes=10,
        doctor_reminder_window_seconds=120,
    )

    # ── 4c. Patch list_due_doctor_reminders → injected rows; run scheduler ───
    with patch.object(booking_repo, "list_due_doctor_reminders", return_value=due_rows):
        with patch.object(scheduler._reminder_keys, "has", return_value=False):
            with patch.object(scheduler._reminder_keys, "add"):
                scheduler._run_reminders_once()

    return sent_docs


# ─── Step 5: Verify sends ─────────────────────────────────────────────────────

def step_verify_sends(sent_docs: list[dict]) -> None:
    print("\n[STEP 5] Verify what was sent")

    check("at least one document was sent", len(sent_docs) >= 1,
          "scheduler did not call send_doc_fn — check DB query, schedule coverage, or T-10 window")

    destinations = {d["to"] for d in sent_docs}
    check("Telegram:8299824956 received the document",
          "telegram:8299824956" in destinations,
          f"sent to: {destinations}")

    import openpyxl
    for doc in sent_docs:
        path = doc["path"]
        full = path if os.path.isabs(path) else os.path.join(str(ROOT), path)
        if os.path.exists(full):
            wb = openpyxl.load_workbook(full)
            ws = wb.active
            rows = ws.max_row - 1
            check(f"Excel sent to {doc['to']} has 48 rows", rows == 48,
                  f"got {rows} rows in {os.path.basename(full)}")


# ─── Step 6: Clean up inserted appointments ───────────────────────────────────

def step_cleanup(config: MySQLConfig, ids: list[int]) -> None:
    if not ids:
        return
    print(f"\n[STEP 6] Clean up {len(ids)} test appointments")
    conn = db_connect(config)
    cur = conn.cursor()
    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('appointment','appointments')")
    tables = [r[0] for r in cur.fetchall()]
    appt_table = "appointment" if "appointment" in tables else "appointments"
    placeholders = ",".join(["%s"] * len(ids))
    cur.execute(f"DELETE FROM {appt_table} WHERE appointment_id IN ({placeholders})", ids)
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    check(f"deleted {deleted} test appointment rows", deleted == len(ids),
          f"expected {len(ids)}, deleted {deleted}")


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("INTEGRATION: Dr. Sanjay T-10 Real Send to Telegram")
    print("=" * 60)

    env_ok = step_verify_env()
    if not env_ok:
        print("\n[ABORT] Fix .env and re-run.")
        sys.exit(1)

    config = get_db_config()
    inserted_ids: list[int] = []

    try:
        doctor_row = step_query_doctor(config)
        sched = step_find_schedule(config)

        if not sched:
            print("\n[ABORT] No schedule found for today. Add a doctor_clinic_schedule row.")
            sys.exit(1)

        inserted_ids = step_insert_appointments(config, sched, doctor_row)
        sent_docs = step_run_scheduler_t10(config, sched, doctor_row, inserted_ids)
        step_verify_sends(sent_docs)

    finally:
        step_cleanup(config, inserted_ids)

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
