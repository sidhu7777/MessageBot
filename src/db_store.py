import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

import mysql.connector


@dataclass
class BookingResult:
    ok: bool
    message: str
    appointment_id: Optional[int] = None


class BookingRepository:
    def __init__(self, database_url: str) -> None:
        parsed = urlparse(database_url.replace("mysql+mysqlconnector://", "mysql://", 1))
        self.user = parsed.username or ""
        self.password = unquote(parsed.password or "")
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 3306
        self.database = (parsed.path or "").lstrip("/")

    @classmethod
    def from_env(cls) -> Optional["BookingRepository"]:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            return None
        if not database_url.startswith("mysql+mysqlconnector://"):
            return None
        return cls(database_url)

    def _connect(self):
        return mysql.connector.connect(
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    def save_confirmed_appointment(self, context) -> BookingResult:
        if not context.patient_name or not context.appointment_date or not context.appointment_time:
            return BookingResult(False, "Missing patient/date/time; appointment not saved.")

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT admin_id FROM admins ORDER BY admin_id LIMIT 1")
            admin_row = cur.fetchone()
            if not admin_row:
                return BookingResult(False, "No admin configured; appointment not saved.")
            admin_id = int(admin_row["admin_id"])

            cur.execute(
                """
                SELECT patient_id
                FROM patients
                WHERE full_name = %s AND admin_id = %s
                ORDER BY patient_id
                LIMIT 1
                """,
                (context.patient_name, admin_id),
            )
            patient_row = cur.fetchone()
            if patient_row:
                patient_id = int(patient_row["patient_id"])
                cur.execute(
                    """
                    UPDATE patients
                    SET age = %s, gender = %s
                    WHERE patient_id = %s
                    """,
                    (context.age, context.gender, patient_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO patients (full_name, age, gender, admin_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (context.patient_name, context.age, context.gender, admin_id),
                )
                patient_id = int(cur.lastrowid)

            cur.execute(
                """
                SELECT s.slot_id, ds.doctor_id, ds.clinic_id
                FROM slots s
                JOIN doctor_clinic_schedule ds ON ds.schedule_id = s.schedule_id
                WHERE s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%%H:%%i') = %s
                  AND s.slot_status = 'AVAILABLE'
                ORDER BY s.slot_id
                LIMIT 1
                """,
                (context.appointment_date, context.appointment_time),
            )
            slot_row = cur.fetchone()
            if not slot_row:
                conn.rollback()
                return BookingResult(False, "No available slot found for selected date and time.")

            slot_id = int(slot_row["slot_id"])
            doctor_id = int(slot_row["doctor_id"])
            clinic_id = int(slot_row["clinic_id"])

            cur.execute(
                """
                INSERT INTO appointments (patient_id, slot_id, doctor_id, clinic_id, admin_id, status)
                VALUES (%s, %s, %s, %s, %s, 'BOOKED')
                """,
                (patient_id, slot_id, doctor_id, clinic_id, admin_id),
            )
            appointment_id = int(cur.lastrowid)

            cur.execute(
                """
                UPDATE slots
                SET slot_status = 'BOOKED'
                WHERE slot_id = %s
                """,
                (slot_id,),
            )

            conn.commit()
            return BookingResult(True, "Appointment persisted to database.", appointment_id=appointment_id)
        finally:
            cur.close()
            conn.close()
