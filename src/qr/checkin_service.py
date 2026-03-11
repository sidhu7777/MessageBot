from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional


@dataclass
class QrCheckinResult:
    status: str
    message: str
    booking_id: Optional[int] = None
    appointment_date: str = ""
    appointment_time: str = ""
    queue_position: Optional[int] = None
    estimated_time: str = ""
    clinic_name: str = ""
    doctor_name: str = ""


class QrCheckinService:
    def __init__(self, booking_repository: Any, scheduling_repository: Any) -> None:
        self.booking_repository = booking_repository
        self.scheduling_repository = scheduling_repository

    @staticmethod
    def _normalize_phone(value: str) -> str:
        raw = (value or "").strip().lower()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :]
        return "".join(ch for ch in raw if ch.isdigit())

    def ensure_schema(self) -> None:
        if not self.booking_repository:
            return
        conn = self.booking_repository._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qr_walkin_queue (
                    queue_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    doctor_id BIGINT NOT NULL,
                    clinic_id BIGINT NOT NULL,
                    patient_name VARCHAR(255) NOT NULL,
                    phone VARCHAR(32) NOT NULL,
                    queue_date DATE NOT NULL,
                    queue_position INT NOT NULL,
                    estimated_time DATETIME NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'WAITING',
                    source_channel VARCHAR(20) NOT NULL DEFAULT 'qr',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_qr_waiting_lookup (doctor_id, clinic_id, queue_date, status, queue_position),
                    UNIQUE KEY uq_qr_waiting_phone (doctor_id, clinic_id, queue_date, phone, status)
                )
                """
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int) -> tuple[str, str]:
        doctor_name = "Doctor"
        clinic_name = "Clinic"
        if not self.booking_repository:
            return doctor_name, clinic_name
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(doctor_name), ''), 'Doctor') AS doctor_name
                FROM doctors
                WHERE doctor_id = %s
                LIMIT 1
                """,
                (doctor_id,),
            )
            row = cur.fetchone() or {}
            doctor_name = str(row.get("doctor_name") or doctor_name)
            cur.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(clinic_name), ''), 'Clinic') AS clinic_name
                FROM clinics
                WHERE clinic_id = %s
                LIMIT 1
                """,
                (clinic_id,),
            )
            row = cur.fetchone() or {}
            clinic_name = str(row.get("clinic_name") or clinic_name)
            return doctor_name, clinic_name
        finally:
            cur.close()
            conn.close()

    def _resolve_admin_id(self, doctor_id: int) -> Optional[int]:
        if not self.booking_repository:
            return None
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT admin_id
                FROM doctors
                WHERE doctor_id = %s
                LIMIT 1
                """,
                (doctor_id,),
            )
            row = cur.fetchone()
            if row and row.get("admin_id") is not None:
                return int(row["admin_id"])
            return self.booking_repository.default_admin_id()
        finally:
            cur.close()
            conn.close()

    def _active_booking(self, phone: str, admin_id: int, doctor_id: int, clinic_id: int) -> Optional[dict]:
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            appointment_table = self.booking_repository._appointment_table()
            phone_expr = self.booking_repository._normalized_phone_sql_expr("p.phone")
            today = datetime.now().date().isoformat()
            params: list[object] = [
                admin_id,
                doctor_id,
                today,
                phone,
            ]
            phone_sql = f"AND ({phone_expr} = %s"
            if len(phone) >= 10:
                phone_sql += f" OR RIGHT({phone_expr}, 10) = %s"
                params.append(phone[-10:])
            phone_sql += ")"
            if self.booking_repository._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        p.booking_id AS booking_number,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    WHERE a.admin_id = %s
                      AND a.doctor_id = %s
                      AND a.appointment_date = %s
                      AND a.clinic_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {phone_sql}
                    ORDER BY a.start_time DESC, a.appointment_id DESC
                    LIMIT 1
                    """,
                    tuple(params[:2] + [today, clinic_id] + params[3:]),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        p.booking_id AS booking_number,
                        DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN slots s ON s.slot_id = a.slot_id
                    WHERE a.admin_id = %s
                      AND a.doctor_id = %s
                      AND s.slot_date = %s
                      AND a.clinic_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {phone_sql}
                    ORDER BY s.slot_time DESC, a.appointment_id DESC
                    LIMIT 1
                    """,
                    tuple(params[:2] + [today, clinic_id] + params[3:]),
                )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    def _first_available_slot(self, doctor_id: int, clinic_id: int, admin_id: int) -> tuple[str, str]:
        slot_date = datetime.now().date().isoformat()
        times = self.scheduling_repository.list_available_times(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            admin_id=admin_id,
            limit=60,
        )
        if times:
            return str(slot_date), str(times[0])
        return "", ""

    def _book_confirmed_overflow(
        self,
        *,
        admin_id: int,
        doctor_id: int,
        clinic_id: int,
        patient_name: str,
        phone: str,
    ) -> tuple[int, int, str, str]:
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        today = datetime.now().date()
        try:
            conn.start_transaction()
            patient_columns = self.booking_repository._table_columns("patients")
            appointment_table = self.booking_repository._appointment_table()
            if not self.booking_repository._use_appointment_mode():
                conn.rollback()
                raise RuntimeError("QR overflow booking currently requires appointment-mode schema.")

            cur.execute(
                """
                SELECT start_time, end_time, slot_duration
                FROM doctor_clinic_schedule
                WHERE doctor_id = %s
                  AND clinic_id = %s
                  AND effective_from <= %s
                  AND effective_to >= %s
                  AND day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                ORDER BY start_time
                """,
                (doctor_id, clinic_id, today, today, today),
            )
            schedules = cur.fetchall()
            normalized = self.booking_repository._normalize_schedules(schedules)
            if not normalized:
                conn.rollback()
                raise RuntimeError("Doctor schedule is not configured for this clinic.")

            total_regular_slots = 0
            slot_duration = normalized[0][2]
            session_end = normalized[-1][1]
            for start_t, end_t, duration in normalized:
                total_regular_slots += int(((datetime.combine(today, end_t) - datetime.combine(today, start_t)).total_seconds() // 60) // duration)
                slot_duration = duration

            phone_expr = self.booking_repository._normalized_phone_sql_expr("phone")
            cur.execute(
                f"""
                SELECT patient_id
                FROM patients
                WHERE admin_id = %s
                  AND full_name = %s
                  AND ({phone_expr} = %s OR RIGHT({phone_expr}, 10) = %s)
                ORDER BY patient_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (admin_id, patient_name, phone, phone[-10:]),
            )
            patient_row = cur.fetchone()
            patient_id = int(patient_row["patient_id"]) if patient_row else None
            if patient_id is None:
                insert_cols = ["full_name", "admin_id", "doctor_id", "phone"]
                insert_vals = [patient_name, admin_id, doctor_id, phone]
                if "patient_type" in patient_columns:
                    insert_cols.append("patient_type")
                    insert_vals.append("existing")
                cur.execute(
                    f"""
                    INSERT INTO patients ({", ".join(insert_cols)})
                    VALUES ({", ".join(["%s"] * len(insert_cols))})
                    """,
                    tuple(insert_vals),
                )
                patient_id = int(cur.lastrowid)
            else:
                update_parts = ["full_name = %s", "phone = %s", "doctor_id = %s"]
                update_vals: list[object] = [patient_name, phone, doctor_id]
                cur.execute(
                    f"""
                    UPDATE patients
                    SET {", ".join(update_parts)}
                    WHERE patient_id = %s
                    """,
                    tuple(update_vals + [patient_id]),
                )

            cur.execute(
                f"""
                SELECT COALESCE(MAX(p.booking_id), 0) AS max_booking_id
                FROM {appointment_table} a
                JOIN patients p ON p.patient_id = a.patient_id
                WHERE a.admin_id = %s
                  AND a.doctor_id = %s
                  AND a.clinic_id = %s
                  AND a.appointment_date = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED', 'COMPLETED')
                """,
                (admin_id, doctor_id, clinic_id, today),
            )
            max_row = cur.fetchone() or {}
            next_booking_id = max(int(max_row.get("max_booking_id") or 0), total_regular_slots) + 1

            overflow_index = next_booking_id - total_regular_slots
            start_dt = datetime.combine(today, session_end) + timedelta(minutes=slot_duration * overflow_index)
            end_dt = start_dt + timedelta(minutes=slot_duration)

            while True:
                start_time = start_dt.time().replace(second=0, microsecond=0)
                end_time = end_dt.time().replace(second=0, microsecond=0)
                cur.execute(
                    f"""
                    SELECT appointment_id, status
                    FROM {appointment_table}
                    WHERE doctor_id = %s
                      AND appointment_date = %s
                      AND start_time = %s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (doctor_id, today, start_time),
                )
                appt_row = cur.fetchone()
                if not appt_row:
                    cur.execute(
                        f"""
                        INSERT INTO {appointment_table}
                        (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time)
                        VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s)
                        """,
                        (patient_id, doctor_id, clinic_id, admin_id, today, start_time, end_time),
                    )
                    appointment_id = int(cur.lastrowid)
                    break

                appt_id = int(appt_row["appointment_id"])
                appt_status = str(appt_row.get("status") or "").upper()
                if appt_status in {"CANCELLED", "COMPLETED"}:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET patient_id = %s,
                            status = 'BOOKED',
                            end_time = %s
                        WHERE appointment_id = %s
                        """,
                        (patient_id, end_time, appt_id),
                    )
                    appointment_id = appt_id
                    break

                next_booking_id += 1
                overflow_index = next_booking_id - total_regular_slots
                start_dt = datetime.combine(today, session_end) + timedelta(minutes=slot_duration * overflow_index)
                end_dt = start_dt + timedelta(minutes=slot_duration)

            if "booking_id" in patient_columns:
                cur.execute(
                    """
                    UPDATE patients
                    SET booking_id = %s
                    WHERE patient_id = %s
                    """,
                    (next_booking_id, patient_id),
                )
            conn.commit()
            return appointment_id, next_booking_id, today.isoformat(), start_time.strftime("%H:%M")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def _enqueue_overflow(
        self,
        *,
        admin_id: int,
        doctor_id: int,
        clinic_id: int,
        patient_name: str,
        phone: str,
    ) -> tuple[int, str]:
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        today = datetime.now().date()
        try:
            conn.start_transaction()
            cur.execute(
                """
                SELECT queue_position, estimated_time
                FROM qr_walkin_queue
                WHERE admin_id = %s
                  AND doctor_id = %s
                  AND clinic_id = %s
                  AND queue_date = %s
                  AND status = 'WAITING'
                  AND phone = %s
                ORDER BY queue_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (admin_id, doctor_id, clinic_id, today, phone),
            )
            existing = cur.fetchone()
            if existing:
                est = existing.get("estimated_time")
                est_text = est.strftime("%H:%M") if est else ""
                conn.commit()
                return int(existing.get("queue_position") or 1), est_text

            cur.execute(
                """
                SELECT COALESCE(MAX(queue_position), 0) AS max_pos
                FROM qr_walkin_queue
                WHERE admin_id = %s
                  AND doctor_id = %s
                  AND clinic_id = %s
                  AND queue_date = %s
                  AND status = 'WAITING'
                FOR UPDATE
                """,
                (admin_id, doctor_id, clinic_id, today),
            )
            max_pos_row = cur.fetchone() or {}
            next_pos = int(max_pos_row.get("max_pos") or 0) + 1
            estimate_dt = datetime.now() + timedelta(minutes=max(10, next_pos * 8))
            cur.execute(
                """
                INSERT INTO qr_walkin_queue
                (admin_id, doctor_id, clinic_id, patient_name, phone, queue_date, queue_position, estimated_time, status, source_channel)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'WAITING', 'qr')
                """,
                (
                    admin_id,
                    doctor_id,
                    clinic_id,
                    patient_name,
                    phone,
                    today,
                    next_pos,
                    estimate_dt,
                ),
            )
            conn.commit()
            return next_pos, estimate_dt.strftime("%H:%M")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def process_checkin(self, *, doctor_id: int, clinic_id: int, patient_name: str, phone: str) -> QrCheckinResult:
        if not self.booking_repository or not self.scheduling_repository:
            return QrCheckinResult(status="error", message="Booking database is not configured.")

        normalized_phone = self._normalize_phone(phone)
        cleaned_name = " ".join((patient_name or "").strip().split())
        if not cleaned_name:
            return QrCheckinResult(status="error", message="Please enter patient name.")
        if len(normalized_phone) < 10 or len(normalized_phone) > 15:
            return QrCheckinResult(status="error", message="Please enter a valid phone number.")

        admin_id = self._resolve_admin_id(doctor_id)
        if not admin_id:
            return QrCheckinResult(status="error", message="Doctor/admin mapping is not configured.")

        doctor_name, clinic_name = self.resolve_doctor_and_clinic(doctor_id=doctor_id, clinic_id=clinic_id)

        active = self._active_booking(
            phone=normalized_phone,
            admin_id=admin_id,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
        )
        if active:
            booking_number = active.get("booking_number") or active.get("appointment_id")
            slot_date = str(active.get("slot_date") or "")
            slot_time = str(active.get("slot_time") or "")
            return QrCheckinResult(
                status="active_booking",
                message=(
                    f"You already have an active booking (#{booking_number})"
                    + (f" on {slot_date} {slot_time}." if slot_date or slot_time else ".")
                ),
                booking_id=int(active.get("appointment_id") or 0) or None,
                appointment_date=slot_date,
                appointment_time=slot_time,
                clinic_name=clinic_name,
                doctor_name=doctor_name,
            )

        slot_date, slot_time = self._first_available_slot(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            admin_id=admin_id,
        )
        if slot_date and slot_time:
            context = SimpleNamespace(
                patient_name=cleaned_name,
                phone_number=normalized_phone,
                clinic_id=str(clinic_id),
                appointment_date=slot_date,
                appointment_time=slot_time,
                reason="QR Walk-in",
                appointment_mode="walk-in",
                booking_for_self=True,
                chat_user_id=None,
                age=None,
                gender=None,
                patient_type="existing",
            )
            save = self.booking_repository.save_confirmed_appointment(
                context=context,
                admin_id=admin_id,
                doctor_id=doctor_id,
            )
            if save.ok:
                number = save.queue_number if save.queue_number is not None else save.appointment_id
                return QrCheckinResult(
                    status="booked",
                    message=f"Appointment confirmed. Patient ID: {number}.",
                    booking_id=save.appointment_id,
                    appointment_date=slot_date,
                    appointment_time=slot_time,
                    clinic_name=clinic_name,
                    doctor_name=doctor_name,
                )

        try:
            appointment_id, booking_number, overflow_date, overflow_time = self._book_confirmed_overflow(
                admin_id=admin_id,
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                patient_name=cleaned_name,
                phone=normalized_phone,
            )
        except Exception as exc:
            return QrCheckinResult(status="error", message=f"Unable to confirm QR booking: {exc}")
        return QrCheckinResult(
            status="booked",
            message=f"Appointment confirmed. Patient ID: {booking_number}.",
            booking_id=appointment_id,
            appointment_date=overflow_date,
            appointment_time=overflow_time,
            clinic_name=clinic_name,
            doctor_name=doctor_name,
        )
