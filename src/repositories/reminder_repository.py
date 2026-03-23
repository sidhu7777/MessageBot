from dataclasses import dataclass
import threading
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class DoctorReminder:
    appointment_id: int
    doctor_id: int
    doctor_whatsapp: str
    doctor_telegram_chat_id: str
    patient_name: str
    patient_contact: str
    clinic_name: str
    slot_date: str
    slot_time: str
    status: str
    booking_number: Optional[int]
    schedule_id: int
    schedule_start_time: str
    schedule_end_time: str


class ReminderRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._meta_cache_lock = threading.Lock()
        self._table_exists_cache: dict[str, bool] = {}
        self._table_columns_cache: dict[str, set[str]] = {}
        self._appointment_table_cache: Optional[str] = None
        self._use_appointment_mode_cache: Optional[bool] = None

    def _connect(self):
        return connect_mysql(self._config)

    def _ensure_meta_cache(self) -> None:
        if not hasattr(self, "_meta_cache_lock"):
            self._meta_cache_lock = threading.Lock()
        if not hasattr(self, "_table_exists_cache"):
            self._table_exists_cache = {}
        if not hasattr(self, "_table_columns_cache"):
            self._table_columns_cache = {}
        if not hasattr(self, "_appointment_table_cache"):
            self._appointment_table_cache = None
        if not hasattr(self, "_use_appointment_mode_cache"):
            self._use_appointment_mode_cache = None

    def _table_exists(self, table_name: str) -> bool:
        self._ensure_meta_cache()
        normalized_name = (table_name or "").strip().lower()
        if not normalized_name:
            return False
        with self._meta_cache_lock:
            cached = self._table_exists_cache.get(normalized_name)
        if cached is not None:
            return bool(cached)
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT 1
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                LIMIT 1
                """,
                (normalized_name,),
            )
            exists = cur.fetchone() is not None
            with self._meta_cache_lock:
                self._table_exists_cache[normalized_name] = bool(exists)
            return bool(exists)
        finally:
            cur.close()
            conn.close()

    def _appointment_table(self) -> str:
        self._ensure_meta_cache()
        with self._meta_cache_lock:
            if self._appointment_table_cache:
                return self._appointment_table_cache
        table_name = "appointment" if self._table_exists("appointment") else "appointments"
        with self._meta_cache_lock:
            self._appointment_table_cache = table_name
        return table_name

    def _use_appointment_mode(self) -> bool:
        self._ensure_meta_cache()
        with self._meta_cache_lock:
            if self._use_appointment_mode_cache is not None:
                return bool(self._use_appointment_mode_cache)
        mode = self._table_exists("appointment") and not self._table_exists("slots")
        with self._meta_cache_lock:
            self._use_appointment_mode_cache = bool(mode)
        return bool(mode)

    def _table_columns(self, table_name: str) -> set[str]:
        self._ensure_meta_cache()
        normalized_name = (table_name or "").strip().lower()
        if not normalized_name:
            return set()
        with self._meta_cache_lock:
            cached = self._table_columns_cache.get(normalized_name)
            if cached is not None:
                return set(cached)
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                """,
                (normalized_name,),
            )
            cols: set[str] = set()
            for row in cur.fetchall():
                if isinstance(row, dict):
                    name = row.get("COLUMN_NAME")
                elif isinstance(row, (list, tuple)) and row:
                    name = row[0]
                else:
                    name = None
                text = str(name or "").strip().lower()
                if text:
                    cols.add(text)
            with self._meta_cache_lock:
                self._table_columns_cache[normalized_name] = set(cols)
            return cols
        finally:
            cur.close()
            conn.close()

    def _invalidate_table_columns_cache(self, *table_names: str) -> None:
        self._ensure_meta_cache()
        with self._meta_cache_lock:
            for table_name in table_names:
                normalized = (table_name or "").strip().lower()
                if normalized:
                    self._table_columns_cache.pop(normalized, None)

    def ensure_reminder_schema(self) -> None:
        """Ensure doctor_remainder_queue table exists with all required columns."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doctor_remainder_queue'
                """
            )
            if cur.fetchone()[0]:
                cur.execute(
                    """
                    SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doctor_remainder_queue'
                    """
                )
                drq_cols = {str(r[0]).lower() for r in cur.fetchall()}
                if "lead_minutes" not in drq_cols:
                    cur.execute(
                        "ALTER TABLE doctor_remainder_queue "
                        "ADD COLUMN lead_minutes INT NOT NULL DEFAULT 10"
                    )
                conn.commit()
                self._invalidate_table_columns_cache("doctor_remainder_queue")
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
            appointment_table = self._appointment_table()
            booking_select = (
                "COALESCE(a.booking_id, p.booking_id)"
                if self._column_exists(appointment_table, "booking_id")
                else "p.booking_id"
            )
            doctor_columns = self._table_columns("doctors")
            whatsapp_col = "whatsapp_number" if "whatsapp_number" in doctor_columns else None
            telegram_col = None
            for candidate in ("telegram_chat_id", "telegram_user_id", "telegram_id", "chat_id", "user_id"):
                if candidate in doctor_columns:
                    telegram_col = candidate
                    break
            whatsapp_select = f"NULLIF(d.{whatsapp_col}, '')" if whatsapp_col else "NULL"
            telegram_select = f"NULLIF(d.{telegram_col}, '')" if telegram_col else "NULL"
            ist_now_sql = "CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')"
            if self._use_appointment_mode():
                params: list[object] = [horizon_minutes]
                admin_sql = ""
                if admin_id is not None:
                    admin_sql = "AND a.admin_id = %s"
                    params.append(admin_id)
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.doctor_id,
                        {whatsapp_select} AS doctor_whatsapp,
                        {telegram_select} AS doctor_telegram_chat_id,
                        COALESCE(p.full_name, '') AS patient_name,
                        COALESCE(p.phone, '') AS patient_contact,
                        COALESCE(c.clinic_name, '') AS clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(a.status, '') AS status,
                        {booking_select} AS booking_number,
                        dcs.schedule_id AS schedule_id,
                        TIME_FORMAT(
                          COALESCE(TIME(STR_TO_DATE(dcs.start_time, '%h:%i %p')), TIME(STR_TO_DATE(dcs.start_time, '%H:%i')), TIME(dcs.start_time)),
                          '%H:%i'
                        ) AS schedule_start_time,
                        TIME_FORMAT(
                          COALESCE(TIME(STR_TO_DATE(dcs.end_time, '%h:%i %p')), TIME(STR_TO_DATE(dcs.end_time, '%H:%i')), TIME(dcs.end_time)),
                          '%H:%i'
                        ) AS schedule_end_time
                    FROM {appointment_table} a
                    LEFT JOIN doctors d ON d.doctor_id = a.doctor_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN doctor_clinic_schedule dcs
                      ON dcs.doctor_id = a.doctor_id
                     AND dcs.clinic_id = a.clinic_id
                     AND dcs.day_of_week = MOD(WEEKDAY(a.appointment_date) + 1, 7)
                     AND dcs.effective_from <= a.appointment_date
                     AND dcs.effective_to >= a.appointment_date
                    WHERE a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      AND TIMESTAMP(a.appointment_date, a.start_time) >= {ist_now_sql}
                      AND TIMESTAMP(a.appointment_date, a.start_time) <= DATE_ADD({ist_now_sql}, INTERVAL %s MINUTE)
                      {admin_sql}
                    ORDER BY doctor_whatsapp, a.appointment_date, a.start_time, a.appointment_id
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                results: list[DoctorReminder] = []
                for row in rows:
                    doctor_whatsapp = str(row.get("doctor_whatsapp") or "").strip()
                    doctor_telegram_chat_id = str(row.get("doctor_telegram_chat_id") or "").strip()
                    if not doctor_whatsapp and not doctor_telegram_chat_id:
                        continue
                    doctor_id = int(row.get("doctor_id") or 0)
                    if doctor_id <= 0:
                        continue
                    schedule_id = int(row.get("schedule_id") or 0)
                    if schedule_id <= 0:
                        continue
                    results.append(
                        DoctorReminder(
                            appointment_id=int(row["appointment_id"]),
                            doctor_id=doctor_id,
                            doctor_whatsapp=doctor_whatsapp,
                            doctor_telegram_chat_id=doctor_telegram_chat_id,
                            patient_name=str(row.get("patient_name") or ""),
                            patient_contact=str(row.get("patient_contact") or ""),
                            clinic_name=str(row.get("clinic_name") or ""),
                            slot_date=str(row.get("slot_date") or ""),
                            slot_time=str(row.get("slot_time") or ""),
                            status=str(row.get("status") or ""),
                            booking_number=int(row["booking_number"]) if row.get("booking_number") is not None else None,
                            schedule_id=schedule_id,
                            schedule_start_time=str(row.get("schedule_start_time") or ""),
                            schedule_end_time=str(row.get("schedule_end_time") or ""),
                        )
                    )
                return results

            params: list[object] = [horizon_minutes]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND a.admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    a.doctor_id,
                    {whatsapp_select} AS doctor_whatsapp,
                    {telegram_select} AS doctor_telegram_chat_id,
                    COALESCE(p.full_name, '') AS patient_name,
                    COALESCE(p.phone, '') AS patient_contact,
                    COALESCE(c.clinic_name, '') AS clinic_name,
                    DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                    COALESCE(a.status, '') AS status,
                    {booking_select} AS booking_number,
                    s.schedule_id AS schedule_id,
                    TIME_FORMAT(dcs.start_time, '%H:%i') AS schedule_start_time,
                    TIME_FORMAT(dcs.end_time, '%H:%i') AS schedule_end_time
                FROM {appointment_table} a
                JOIN slots s ON s.slot_id = a.slot_id
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                LEFT JOIN doctors d ON d.doctor_id = a.doctor_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN patients p ON p.patient_id = a.patient_id
                WHERE a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                  AND s.slot_status = 'BOOKED'
                  AND s.schedule_id IS NOT NULL
                  AND TIMESTAMP(s.slot_date, s.slot_time) >= {ist_now_sql}
                  AND TIMESTAMP(s.slot_date, s.slot_time) <= DATE_ADD({ist_now_sql}, INTERVAL %s MINUTE)
                  {admin_sql}
                ORDER BY doctor_whatsapp, s.slot_date, s.schedule_id, s.slot_time, a.appointment_id
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            results: list[DoctorReminder] = []
            for row in rows:
                doctor_whatsapp = str(row.get("doctor_whatsapp") or "").strip()
                doctor_telegram_chat_id = str(row.get("doctor_telegram_chat_id") or "").strip()
                if not doctor_whatsapp and not doctor_telegram_chat_id:
                    continue
                doctor_id = int(row.get("doctor_id") or 0)
                if doctor_id <= 0:
                    continue
                schedule_id = int(row.get("schedule_id") or 0)
                if schedule_id <= 0:
                    continue
                results.append(
                    DoctorReminder(
                        appointment_id=int(row["appointment_id"]),
                        doctor_id=doctor_id,
                        doctor_whatsapp=doctor_whatsapp,
                        doctor_telegram_chat_id=doctor_telegram_chat_id,
                        patient_name=str(row.get("patient_name") or ""),
                        patient_contact=str(row.get("patient_contact") or ""),
                        clinic_name=str(row.get("clinic_name") or ""),
                        slot_date=str(row.get("slot_date") or ""),
                        slot_time=str(row.get("slot_time") or ""),
                        status=str(row.get("status") or ""),
                        booking_number=int(row["booking_number"]) if row.get("booking_number") is not None else None,
                        schedule_id=schedule_id,
                        schedule_start_time=str(row.get("schedule_start_time") or ""),
                        schedule_end_time=str(row.get("schedule_end_time") or ""),
                    )
                )
            return results
        finally:
            cur.close()
            conn.close()

    def get_extra_doctor_contacts(self, doctor_ids: list[int]) -> dict[int, list[dict]]:
        """Return additional WhatsApp numbers and Telegram chat IDs from
        doctor_whatsapp_numbers for the given doctor IDs.

        Returns::
            {doctor_id: [{"whatsapp": "raw_number", "telegram": "raw_chat_id"}, ...]}

        Returns empty dict if the table does not exist or doctor_ids is empty.
        Duplicate whatsapp+telegram pairs for the same doctor are collapsed.
        """
        if not doctor_ids:
            return {}
        # Guard: table may not exist in all deployments
        try:
            table_cols = self._table_columns("doctor_whatsapp_numbers")
        except Exception:
            return {}
        if not table_cols:
            return {}

        whatsapp_col = "whatsapp_number" if "whatsapp_number" in table_cols else None
        chat_id_col = "chat_id" if "chat_id" in table_cols else None
        if not whatsapp_col and not chat_id_col:
            return {}

        wa_select = f"NULLIF({whatsapp_col}, '')" if whatsapp_col else "NULL"
        tg_select = f"NULLIF({chat_id_col}, '')" if chat_id_col else "NULL"

        # Build IN (...) safely with one placeholder per id
        placeholders = ", ".join(["%s"] * len(doctor_ids))
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT doctor_id,
                       {wa_select}  AS whatsapp_number,
                       {tg_select}  AS telegram_chat_id
                FROM doctor_whatsapp_numbers
                WHERE doctor_id IN ({placeholders})
                """,
                tuple(int(d) for d in doctor_ids),
            )
            result: dict[int, list[dict]] = {}
            seen: dict[int, set[tuple]] = {}   # dedup per doctor
            for row in cur.fetchall():
                did = int(row.get("doctor_id") or 0)
                if did <= 0:
                    continue
                wa = str(row.get("whatsapp_number") or "").strip()
                tg = str(row.get("telegram_chat_id") or "").strip()
                if not wa and not tg:
                    continue
                key = (wa, tg)
                if key in seen.get(did, set()):
                    continue
                seen.setdefault(did, set()).add(key)
                result.setdefault(did, []).append({"whatsapp": wa, "telegram": tg})
            return result
        finally:
            cur.close()
            conn.close()

    def is_reminder_sent(self, *, dedup_key: str) -> bool:
        """Return True if a SENT row already exists for this dedup_key."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT status FROM doctor_remainder_queue WHERE dedup_key=%s LIMIT 1",
                (dedup_key,),
            )
            row = cur.fetchone()
            return row is not None and str(row[0]).upper() == "SENT"
        finally:
            cur.close()
            conn.close()

    def insert_or_get_reminder_queue(
        self,
        *,
        doctor_id: int,
        schedule_id: int,
        slot_date: str,
        schedule_start_time: str,
        schedule_end_time: str,
        channel: str,
        destination: str,
        lead_minutes: int,
        dedup_key: str,
    ) -> int:
        """Insert a PENDING row into doctor_remainder_queue and return queue_id.
        If the dedup_key already exists, return the existing queue_id."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT queue_id FROM doctor_remainder_queue WHERE dedup_key=%s LIMIT 1",
                (dedup_key,),
            )
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                """
                INSERT INTO doctor_remainder_queue
                    (doctor_id, schedule_id, slot_date, schedule_start_time,
                     schedule_end_time, channel, destination, lead_minutes,
                     status, dedup_key, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', %s, CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'))
                """,
                (
                    doctor_id, schedule_id, slot_date,
                    schedule_start_time, schedule_end_time,
                    channel, destination, lead_minutes, dedup_key,
                ),
            )
            queue_id = cur.lastrowid
            conn.commit()
            return int(queue_id)
        finally:
            cur.close()
            conn.close()

    def mark_reminder_sent(self, *, queue_id: int) -> None:
        """Mark a doctor_remainder_queue row as SENT."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE doctor_remainder_queue "
                "SET status='SENT', sent_at=CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'), "
                "updated_at=CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30') "
                "WHERE queue_id=%s",
                (queue_id,),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def mark_reminder_failed(self, *, queue_id: int, error: str) -> None:
        """Mark a doctor_remainder_queue row as FAILED and increment attempt_count."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE doctor_remainder_queue "
                "SET status='FAILED', last_error=%s, "
                "attempt_count=attempt_count+1, updated_at=CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30') "
                "WHERE queue_id=%s",
                (str(error)[:250], queue_id),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
