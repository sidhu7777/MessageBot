"""
Live integration test: verify appointment.notify_telegram_chat_id is used for doctor-cancel notifications.

What this does (REAL DB + REAL Telegram send attempt):
1) Creates a test patient + appointment with notify_telegram_chat_id
2) Calls BookingRepository.cancel_appointment(..., cancelled_by='DOCTOR')
3) Runs one scheduler event notification cycle
4) Verifies notification rows and SENT status in DB

Run:
  python tests/integration_notify_chatid_doctor_cancel_real.py

Optional:
  set TEST_TELEGRAM_CHAT_ID env var (defaults to 8299824956)
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from main import automation_scheduler, booking_repository

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{suffix}")


def _pick_doctor_clinic_admin(cur) -> tuple[int, int, int]:
    cur.execute(
        """
        SELECT dcs.doctor_id, dcs.clinic_id, d.admin_id
        FROM doctor_clinic_schedule dcs
        JOIN doctors d ON d.doctor_id = dcs.doctor_id
        ORDER BY dcs.schedule_id
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No doctor_clinic_schedule row found in DB.")
    return int(row["doctor_id"]), int(row["clinic_id"]), int(row["admin_id"])


def main() -> int:
    print("=" * 68)
    print("INTEGRATION: notify_telegram_chat_id + doctor cancel event")
    print("=" * 68)

    target_chat_id = str(os.getenv("TEST_TELEGRAM_CHAT_ID", "8299824956")).strip()
    print(f"Target chat id: {target_chat_id}")

    conn = booking_repository._connect()
    cur = conn.cursor(dictionary=True)

    patient_id = None
    appointment_id = None

    try:
        doctor_id, clinic_id, admin_id = _pick_doctor_clinic_admin(cur)
        print(f"Using doctor_id={doctor_id}, clinic_id={clinic_id}, admin_id={admin_id}")

        stamp = int(time.time())
        test_name = f"NotifyChat Test {stamp}"
        test_phone = f"9{str(stamp)[-9:]}"

        cur.execute(
            """
            SELECT patient_id
            FROM patients
            WHERE telegram_chat_id = %s
            ORDER BY patient_id DESC
            LIMIT 1
            """,
            (target_chat_id,),
        )
        existing_patient = cur.fetchone()
        if existing_patient:
            patient_id = int(existing_patient["patient_id"])
            cur.execute(
                """
                UPDATE patients
                SET admin_id = %s,
                    doctor_id = %s,
                    phone = %s
                WHERE patient_id = %s
                """,
                (admin_id, doctor_id, test_phone, patient_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO patients (full_name, admin_id, doctor_id, phone, telegram_chat_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (test_name, admin_id, doctor_id, test_phone, target_chat_id),
            )
            patient_id = int(cur.lastrowid)

        start_dt = datetime.now() + timedelta(minutes=35)
        end_dt = start_dt + timedelta(minutes=15)
        appt_date = start_dt.date().isoformat()
        start_time = start_dt.strftime("%H:%M:%S")
        end_time = end_dt.strftime("%H:%M:%S")

        cur.execute(
            """
            INSERT INTO appointment
            (
                patient_id, doctor_id, clinic_id, admin_id,
                status, appointment_date, start_time, end_time,
                notify_telegram_chat_id
            )
            VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s, %s)
            """,
            (
                patient_id,
                doctor_id,
                clinic_id,
                admin_id,
                appt_date,
                start_time,
                end_time,
                target_chat_id,
            ),
        )
        appointment_id = int(cur.lastrowid)
        conn.commit()

        print(f"Created appointment_id={appointment_id} with notify_telegram_chat_id={target_chat_id}")

        # Check appointment column persisted
        cur.execute(
            """
            SELECT notify_telegram_chat_id
            FROM appointment
            WHERE appointment_id = %s
            """,
            (appointment_id,),
        )
        row = cur.fetchone() or {}
        persisted_notify = str(row.get("notify_telegram_chat_id") or "").strip()
        check(
            "Appointment has notify_telegram_chat_id",
            persisted_notify == target_chat_id,
            f"stored={persisted_notify}",
        )

        # Doctor cancel via production repository path
        cancel_ok = booking_repository.cancel_appointment(
            appointment_id=appointment_id,
            admin_id=admin_id,
            cancelled_by="DOCTOR",
        )
        check("cancel_appointment(cancelled_by=DOCTOR) succeeded", bool(cancel_ok))

        # Run one event dispatch cycle (REAL send attempt)
        automation_scheduler._run_event_notifications_once()

        # Refresh connection so we see latest rows committed by scheduler worker updates.
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
        conn = booking_repository._connect()
        cur = conn.cursor(dictionary=True)

        rows = []
        for _ in range(10):
            cur.execute(
                """
                SELECT
                    notification_id,
                    event_type,
                    channel,
                    destination,
                    status,
                    provider_message_sid,
                    error_text,
                    attempt_count,
                    dead_at
                FROM appointment_notification_log
                WHERE appointment_id = %s
                ORDER BY notification_id
                """,
                (appointment_id,),
            )
            rows = cur.fetchall()
            if rows and all(str(r.get("status") or "").upper() in {"SENT", "FAILED", "DEAD"} for r in rows):
                break
            time.sleep(1.0)

        print(f"Notification rows for appointment {appointment_id}: {len(rows)}")
        for r in rows:
            print(
                f"  id={r['notification_id']} event={r['event_type']} channel={r['channel']} "
                f"dest={r['destination']} status={r['status']} attempts={r['attempt_count']}"
            )

        check("At least one notification row created", len(rows) >= 1)

        # Destination correctness check
        has_target_dest = any(
            (str(r.get("destination") or "").strip() == f"telegram:{target_chat_id}")
            for r in rows
        )
        check(
            "At least one row has destination telegram:<target_chat_id>",
            has_target_dest,
        )

        sent_rows = [r for r in rows if str(r.get("status") or "").upper() == "SENT"]
        check(
            "At least one notification SENT",
            len(sent_rows) >= 1,
            "No SENT rows; check bot token/chat permissions or scheduler fallback failures",
        )

        if len(rows) > 1:
            print("[INFO] Multiple rows found for same appointment event.")
            print("[INFO] If both trigger + app logging are enabled, duplicates are expected.")

    except Exception as exc:
        print(f"\n[ERROR] Test failed with exception: {exc}")
        global FAIL
        FAIL += 1
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

    print("\n" + "=" * 68)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 68)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
