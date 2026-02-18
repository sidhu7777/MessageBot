from dataclasses import dataclass
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class BookingResult:
    ok: bool
    message: str
    appointment_id: Optional[int] = None


@dataclass
class DoctorReminder:
    appointment_id: int
    doctor_whatsapp: str
    patient_name: str
    clinic_name: str
    slot_date: str
    slot_time: str
    schedule_id: int
    schedule_start_time: str
    schedule_end_time: str


class BookingRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config

    def _connect(self):
        return connect_mysql(self._config)

    def default_admin_id(self) -> Optional[int]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT admin_id FROM admins ORDER BY admin_id LIMIT 1")
            row = cur.fetchone()
            return int(row["admin_id"]) if row else None
        finally:
            cur.close()
            conn.close()

    def find_active_appointment_by_patient_name(
        self,
        patient_name: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[dict]:
        if not patient_name:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            params: list[object] = [patient_name, actual_admin_id]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND a.doctor_id = %s"
                params.append(doctor_id)
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    a.clinic_id,
                    a.doctor_id,
                    c.clinic_name,
                    s.slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time
                FROM appointments a
                JOIN patients p ON p.patient_id = a.patient_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN slots s ON s.slot_id = a.slot_id
                WHERE p.full_name = %s
                  AND a.admin_id = %s
                  AND a.status = 'BOOKED'
                  {doctor_sql}
                ORDER BY a.appointment_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    def cancel_appointment(self, appointment_id: int, admin_id: Optional[int] = None) -> bool:
        if not appointment_id:
            return False
        conn = self._connect()
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                conn.rollback()
                return False
            cur.execute(
                """
                SELECT slot_id
                FROM appointments
                WHERE appointment_id = %s
                  AND admin_id = %s
                  AND status = 'BOOKED'
                LIMIT 1
                FOR UPDATE
                """,
                (appointment_id, actual_admin_id),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False

            slot_id = int(row["slot_id"]) if row.get("slot_id") is not None else None
            cur.execute(
                """
                UPDATE appointments
                SET status = 'CANCELLED'
                WHERE appointment_id = %s
                  AND admin_id = %s
                  AND status = 'BOOKED'
                """,
                (appointment_id, actual_admin_id),
            )
            if cur.rowcount <= 0:
                conn.rollback()
                return False

            if slot_id:
                cur.execute(
                    """
                    UPDATE slots
                    SET slot_status = 'AVAILABLE'
                    WHERE slot_id = %s
                    """,
                    (slot_id,),
                )

            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            cur.close()
            conn.close()

    def reschedule_appointment_same_clinic(
        self,
        appointment_id: int,
        new_date: str,
        new_time: str,
        admin_id: Optional[int] = None,
    ) -> BookingResult:
        if not appointment_id or not new_date or not new_time:
            return BookingResult(False, "Missing required reschedule fields.")
        conn = self._connect()
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                conn.rollback()
                return BookingResult(False, "No admin configured.")

            cur.execute(
                """
                SELECT appointment_id, slot_id, clinic_id, doctor_id
                FROM appointments
                WHERE appointment_id = %s
                  AND admin_id = %s
                  AND status = 'BOOKED'
                LIMIT 1
                FOR UPDATE
                """,
                (appointment_id, actual_admin_id),
            )
            current = cur.fetchone()
            if not current:
                conn.rollback()
                return BookingResult(False, "Active appointment not found.")

            current_slot_id = int(current["slot_id"])
            clinic_id = int(current["clinic_id"])
            doctor_id = int(current["doctor_id"])

            cur.execute(
                """
                SELECT
                    s.slot_id,
                    dcs.doctor_id,
                    dcs.clinic_id
                FROM slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                WHERE dcs.clinic_id = %s
                  AND dcs.doctor_id = %s
                  AND s.admin_id = %s
                  AND s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%H:%i') = %s
                  AND s.slot_status = 'AVAILABLE'
                ORDER BY s.slot_id
                LIMIT 1
                FOR UPDATE
                """,
                (clinic_id, doctor_id, actual_admin_id, new_date, new_time),
            )
            target = cur.fetchone()
            if not target:
                conn.rollback()
                return BookingResult(False, "Requested new slot is not available.")

            new_slot_id = int(target["slot_id"])
            new_doctor_id = int(target["doctor_id"])
            new_clinic_id = int(target["clinic_id"])

            cur.execute("UPDATE slots SET slot_status = 'AVAILABLE' WHERE slot_id = %s", (current_slot_id,))
            cur.execute("UPDATE slots SET slot_status = 'BOOKED' WHERE slot_id = %s", (new_slot_id,))
            cur.execute(
                """
                UPDATE appointments
                SET slot_id = %s,
                    doctor_id = %s,
                    clinic_id = %s
                WHERE appointment_id = %s
                  AND admin_id = %s
                """,
                (new_slot_id, new_doctor_id, new_clinic_id, appointment_id, actual_admin_id),
            )
            conn.commit()
            return BookingResult(True, "Appointment rescheduled.", appointment_id=appointment_id)
        except Exception as exc:
            conn.rollback()
            return BookingResult(False, f"Reschedule transaction failed: {exc}")
        finally:
            cur.close()
            conn.close()

    def save_confirmed_appointment(
        self,
        context,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> BookingResult:
        if (
            not context.patient_name
            or not context.appointment_date
            or not context.appointment_time
            or not context.clinic_id
        ):
            return BookingResult(False, "Missing required fields; appointment not saved.")

        conn = self._connect()
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                conn.rollback()
                return BookingResult(False, "No admin configured.")

            # Upsert-like patient lookup by name/admin.
            cur.execute(
                """
                SELECT patient_id
                FROM patients
                WHERE full_name = %s AND admin_id = %s
                ORDER BY patient_id
                LIMIT 1
                """,
                (context.patient_name, actual_admin_id),
            )
            patient_row = cur.fetchone()
            if patient_row:
                patient_id = int(patient_row["patient_id"])
                cur.execute(
                    """
                    UPDATE patients
                    SET age = %s,
                        gender = %s,
                        phone = %s,
                        patient_type = %s,
                        reason = %s,
                        symptoms = %s
                    WHERE patient_id = %s
                    """,
                    (
                        context.age,
                        context.gender,
                        context.phone_number,
                        context.patient_type,
                        context.reason,
                        context.symptoms,
                        patient_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO patients
                    (full_name, age, gender, phone, admin_id, patient_type, reason, symptoms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        context.patient_name,
                        context.age,
                        context.gender,
                        context.phone_number,
                        actual_admin_id,
                        context.patient_type,
                        context.reason,
                        context.symptoms,
                    ),
                )
                patient_id = int(cur.lastrowid)

            # Idempotency guard: if same patient already has the same booked slot, return existing appointment.
            params: list[object] = [
                patient_id,
                int(context.clinic_id),
                actual_admin_id,
            ]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND a.doctor_id = %s"
                params.append(doctor_id)
            params.extend([context.appointment_date, context.appointment_time])
            cur.execute(
                f"""
                SELECT a.appointment_id
                FROM appointments a
                JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.patient_id = %s
                  AND a.clinic_id = %s
                  AND a.admin_id = %s
                  AND a.status = 'BOOKED'
                  {doctor_sql}
                  AND s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%H:%i') = %s
                ORDER BY a.appointment_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return BookingResult(
                    True,
                    "Appointment already exists.",
                    appointment_id=int(existing["appointment_id"]),
                )

            params = [int(context.clinic_id), actual_admin_id]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND dcs.doctor_id = %s"
                params.append(doctor_id)
            params.extend([context.appointment_date, context.appointment_time])
            cur.execute(
                f"""
                SELECT
                    s.slot_id,
                    dcs.doctor_id,
                    dcs.clinic_id
                FROM slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                WHERE dcs.clinic_id = %s
                  AND s.admin_id = %s
                  {doctor_sql}
                  AND s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%H:%i') = %s
                  AND s.slot_status = 'AVAILABLE'
                ORDER BY s.slot_id
                LIMIT 1
                FOR UPDATE
                """,
                tuple(params),
            )
            slot_row = cur.fetchone()
            if not slot_row:
                conn.rollback()
                return BookingResult(False, "Selected slot is not available.")

            slot_id = int(slot_row["slot_id"])
            doctor_id = int(slot_row["doctor_id"])
            clinic_id = int(slot_row["clinic_id"])

            cur.execute(
                """
                UPDATE slots
                SET slot_status = 'BOOKED'
                WHERE slot_id = %s
                """,
                (slot_id,),
            )

            cur.execute(
                """
                INSERT INTO appointments
                (patient_id, slot_id, doctor_id, clinic_id, admin_id, status)
                VALUES (%s, %s, %s, %s, %s, 'BOOKED')
                """,
                (patient_id, slot_id, doctor_id, clinic_id, actual_admin_id),
            )
            appointment_id = int(cur.lastrowid)
            conn.commit()
            return BookingResult(True, "Appointment persisted.", appointment_id=appointment_id)
        except Exception as exc:
            conn.rollback()
            return BookingResult(False, f"Booking transaction failed: {exc}")
        finally:
            cur.close()
            conn.close()

    def get_appointment_status(self, appointment_id: int) -> Optional[dict]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    a.appointment_id,
                    a.status,
                    p.full_name AS patient_name,
                    c.clinic_name,
                    s.slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time
                FROM appointments a
                LEFT JOIN patients p ON p.patient_id = a.patient_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.appointment_id = %s
                """,
                (appointment_id,),
            )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    def list_due_doctor_reminders(
        self,
        lookahead_minutes: int = 180,
        admin_id: Optional[int] = None,
    ) -> list[DoctorReminder]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            horizon_minutes = max(1, int(lookahead_minutes))
            params: list[object] = [horizon_minutes]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND a.admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    NULLIF(d.whatsapp_number, '') AS doctor_whatsapp,
                    COALESCE(p.full_name, '') AS patient_name,
                    COALESCE(c.clinic_name, '') AS clinic_name,
                    DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                    s.schedule_id AS schedule_id,
                    TIME_FORMAT(dcs.start_time, '%H:%i') AS schedule_start_time,
                    TIME_FORMAT(dcs.end_time, '%H:%i') AS schedule_end_time
                FROM appointments a
                JOIN slots s ON s.slot_id = a.slot_id
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                LEFT JOIN doctors d ON d.doctor_id = a.doctor_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN patients p ON p.patient_id = a.patient_id
                WHERE a.status = 'BOOKED'
                  AND s.slot_status = 'BOOKED'
                  AND s.schedule_id IS NOT NULL
                  AND TIMESTAMP(s.slot_date, s.slot_time) >= NOW()
                  AND TIMESTAMP(s.slot_date, s.slot_time) <= DATE_ADD(NOW(), INTERVAL %s MINUTE)
                  {admin_sql}
                ORDER BY doctor_whatsapp, s.slot_date, s.schedule_id, s.slot_time, a.appointment_id
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            results: list[DoctorReminder] = []
            for row in rows:
                doctor_whatsapp = str(row.get("doctor_whatsapp") or "").strip()
                if not doctor_whatsapp:
                    continue
                schedule_id = int(row.get("schedule_id") or 0)
                if schedule_id <= 0:
                    continue
                results.append(
                    DoctorReminder(
                        appointment_id=int(row["appointment_id"]),
                        doctor_whatsapp=doctor_whatsapp,
                        patient_name=str(row.get("patient_name") or ""),
                        clinic_name=str(row.get("clinic_name") or ""),
                        slot_date=str(row.get("slot_date") or ""),
                        slot_time=str(row.get("slot_time") or ""),
                        schedule_id=schedule_id,
                        schedule_start_time=str(row.get("schedule_start_time") or ""),
                        schedule_end_time=str(row.get("schedule_end_time") or ""),
                    )
                )
            return results
        finally:
            cur.close()
            conn.close()
