from dataclasses import dataclass
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class BookingResult:
    ok: bool
    message: str
    appointment_id: Optional[int] = None


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
    ) -> Optional[dict]:
        if not patient_name:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            cur.execute(
                """
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
                ORDER BY a.appointment_id DESC
                LIMIT 1
                """,
                (patient_name, actual_admin_id),
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

    def save_confirmed_appointment(self, context, admin_id: Optional[int] = None) -> BookingResult:
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
                        patient_type = %s,
                        reason = %s,
                        symptoms = %s
                    WHERE patient_id = %s
                    """,
                    (
                        context.age,
                        context.gender,
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
                    (full_name, age, gender, admin_id, patient_type, reason, symptoms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        context.patient_name,
                        context.age,
                        context.gender,
                        actual_admin_id,
                        context.patient_type,
                        context.reason,
                        context.symptoms,
                    ),
                )
                patient_id = int(cur.lastrowid)

            # Idempotency guard: if same patient already has the same booked slot, return existing appointment.
            cur.execute(
                """
                SELECT a.appointment_id
                FROM appointments a
                JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.patient_id = %s
                  AND a.clinic_id = %s
                  AND a.admin_id = %s
                  AND a.status = 'BOOKED'
                  AND s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%H:%i') = %s
                ORDER BY a.appointment_id DESC
                LIMIT 1
                """,
                (
                    patient_id,
                    int(context.clinic_id),
                    actual_admin_id,
                    context.appointment_date,
                    context.appointment_time,
                ),
            )
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return BookingResult(
                    True,
                    "Appointment already exists.",
                    appointment_id=int(existing["appointment_id"]),
                )

            cur.execute(
                """
                SELECT
                    s.slot_id,
                    dcs.doctor_id,
                    dcs.clinic_id
                FROM slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                WHERE dcs.clinic_id = %s
                  AND s.admin_id = %s
                  AND s.slot_date = %s
                  AND TIME_FORMAT(s.slot_time, '%H:%i') = %s
                  AND s.slot_status = 'AVAILABLE'
                ORDER BY s.slot_id
                LIMIT 1
                FOR UPDATE
                """,
                (
                    int(context.clinic_id),
                    actual_admin_id,
                    context.appointment_date,
                    context.appointment_time,
                ),
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
