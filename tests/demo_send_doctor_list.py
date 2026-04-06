import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from twilio.rest import Client

from src.automation.scheduler import AutomationScheduler
from src.config import load_settings
from src.db.connection import parse_mysql_url
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository


def _connect_mysql(config):
    import mysql.connector

    return mysql.connector.connect(
        user=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
        database=config.database,
        autocommit=True,
    )


def main() -> None:
    load_dotenv()
    settings = load_settings()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing.")
    config = parse_mysql_url(database_url)

    if not settings.twilio_account_sid or not settings.twilio_auth_token or not settings.twilio_whatsapp_from:
        raise RuntimeError("Twilio env vars missing: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_NUMBER")

    doctor_id = int(os.getenv("DEMO_DOCTOR_ID", "7"))
    demo_count = int(os.getenv("DEMO_PATIENT_COUNT", "12"))
    admin_id = int(os.getenv("DEMO_ADMIN_ID", "1"))

    conn = _connect_mysql(config)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT clinic_id
            FROM clinics
            WHERE doctor_id = %s AND status = 'ACTIVE'
            ORDER BY clinic_id
            LIMIT 1
            """,
            (doctor_id,),
        )
        clinic_row = cur.fetchone()
        if not clinic_row:
            raise RuntimeError(f"No active clinic mapped for doctor_id={doctor_id}.")
        clinic_id = int(clinic_row["clinic_id"])

        cur.execute(
            """
            SELECT whatsapp_number
            FROM doctors
            WHERE doctor_id = %s
            LIMIT 1
            """,
            (doctor_id,),
        )
        doctor_row = cur.fetchone()
        doctor_whatsapp = (doctor_row or {}).get("whatsapp_number")
        if not doctor_whatsapp:
            raise RuntimeError(f"doctor_id={doctor_id} has empty whatsapp_number.")

        now = datetime.now()
        start_dt = now + timedelta(minutes=2)
        end_dt = start_dt + timedelta(hours=1)

        start_time = start_dt.strftime("%H:%M:00")
        end_time = end_dt.strftime("%H:%M:00")
        weekday = start_dt.weekday()
        effective_from = start_dt.strftime("%Y-%m-%d")
        effective_to = (start_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        cur.execute(
            """
            INSERT INTO doctor_clinic_schedule
            (doctor_id, clinic_id, admin_id, start_time, end_time, slot_duration, day_of_week, effective_from, effective_to)
            VALUES (%s, %s, %s, %s, %s, 5, %s, %s, %s)
            """,
            (
                doctor_id,
                clinic_id,
                admin_id,
                start_time,
                end_time,
                weekday,
                effective_from,
                effective_to,
            ),
        )
        schedule_id = int(cur.lastrowid)

        scheduling_repo = SchedulingRepository(config)
        booking_repo = BookingRepository(config)
        scheduling_repo.generate_slots_for_schedule(schedule_id=schedule_id, days_ahead=1)

        cur.execute(
            """
            SELECT slot_id, TIME_FORMAT(slot_time, '%H:%i') AS hhmm
            FROM slots
            WHERE schedule_id = %s
              AND slot_date = %s
              AND slot_status = 'AVAILABLE'
            ORDER BY slot_time
            LIMIT %s
            """,
            (schedule_id, effective_from, demo_count),
        )
        slot_rows = cur.fetchall()
        if len(slot_rows) < demo_count:
            raise RuntimeError(
                f"Only {len(slot_rows)} available slots found for schedule_id={schedule_id}, expected {demo_count}."
            )

        tag = now.strftime("%H%M%S")
        patient_ids: list[int] = []
        for idx in range(1, demo_count + 1):
            name = f"DemoList_{tag}_{idx:02d}"
            phone = f"9{idx:09d}"[-10:]
            cur.execute(
                """
                INSERT INTO patients
                (full_name, age, gender, admin_id, patient_type, reason, symptoms, phone, doctor_id)
                VALUES (%s, 30, 'Male', %s, 'New', 'Demo checkup', 'Demo symptom', %s, %s)
                """,
                (name, admin_id, phone, doctor_id),
            )
            patient_ids.append(int(cur.lastrowid))

        for patient_id, slot_row in zip(patient_ids, slot_rows):
            slot_id = int(slot_row["slot_id"])
            cur.execute("UPDATE slots SET slot_status = 'BOOKED' WHERE slot_id = %s", (slot_id,))
            cur.execute(
                """
                INSERT INTO appointments
                (patient_id, slot_id, doctor_id, clinic_id, admin_id, status)
                VALUES (%s, %s, %s, %s, %s, 'BOOKED')
                """,
                (patient_id, slot_id, doctor_id, clinic_id, admin_id),
            )

        twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

        def _send(to_number: str, body: str) -> None:
            twilio_client.messages.create(
                from_=settings.twilio_whatsapp_from,
                to=to_number,
                body=body,
            )

        scheduler = AutomationScheduler(
            booking_repository=booking_repo,
            scheduling_repository=None,
            send_message_fn=_send,
            source_whatsapp_number=settings.twilio_whatsapp_from,
            enabled=True,
            doctor_reminder_enabled=True,
            doctor_reminder_lead_minutes=0,
            doctor_reminder_window_seconds=600,
        )
        scheduler._run_reminders_once()

        print("Demo reminder send triggered.")
        print(f"doctor_id={doctor_id}")
        print(f"schedule_id={schedule_id}")
        print(f"slot_window={effective_from} {start_time}-{end_time}")
        print(f"patients_created={demo_count}")
        print(f"doctor_whatsapp={doctor_whatsapp}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
