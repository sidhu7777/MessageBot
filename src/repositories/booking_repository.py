from dataclasses import dataclass
from datetime import datetime, timedelta, date, time
import threading
from typing import Optional
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class BookingResult:
    ok: bool
    message: str
    appointment_id: Optional[int] = None
    queue_number: Optional[int] = None


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


@dataclass
class NotificationEvent:
    notification_id: int
    appointment_id: int
    event_type: str
    channel: str
    destination: str
    status: str
    patient_name: str
    clinic_name: str
    slot_date: str
    slot_time: str
    patient_phone: str
    patient_telegram_chat_id: str
    meta_json: str
    admin_id: Optional[int]
    attempt_count: int = 0


class BookingRepository:
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

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        normalized_column = (column_name or "").strip().lower()
        if not normalized_column:
            return False
        return normalized_column in self._table_columns(table_name)

    def _first_existing_column(self, table_name: str, candidates: tuple[str, ...]) -> Optional[str]:
        cols = self._table_columns(table_name)
        for candidate in candidates:
            normalized = (candidate or "").strip().lower()
            if normalized and normalized in cols:
                return normalized
        return None

    def _invalidate_table_columns_cache(self, *table_names: str) -> None:
        self._ensure_meta_cache()
        with self._meta_cache_lock:
            for table_name in table_names:
                normalized = (table_name or "").strip().lower()
                if normalized:
                    self._table_columns_cache.pop(normalized, None)

    @staticmethod
    def _normalized_phone_sql_expr(column_expr: str) -> str:
        return (
            f"REPLACE(REPLACE(REPLACE(REPLACE(LOWER(COALESCE({column_expr}, '')), 'whatsapp:', ''), '+', ''), '-', ''), ' ', '')"
        )

    def ensure_notification_schema(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            appointment_table = self._appointment_table()
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                """,
                (appointment_table,),
            )
            cols = {str(row[0]).lower() for row in cur.fetchall()}

            if "cancelled_by" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN cancelled_by VARCHAR(20) NULL")
            if "rescheduled_by" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN rescheduled_by VARCHAR(20) NULL")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS appointment_notification_log (
                    notification_id BIGINT NOT NULL AUTO_INCREMENT,
                    appointment_id INT NOT NULL,
                    event_type VARCHAR(40) NOT NULL,
                    channel VARCHAR(30) NOT NULL,
                    destination VARCHAR(120) NULL,
                    status VARCHAR(20) NOT NULL,
                    error_text TEXT NULL,
                    meta_json TEXT NULL,
                    admin_id INT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at DATETIME NULL,
                    PRIMARY KEY (notification_id),
                    KEY idx_notification_appointment (appointment_id),
                    KEY idx_notification_event (event_type, created_at),
                    KEY idx_notification_status (status, created_at)
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'appointment_notification_log'
                """
            )
            log_cols = {str(row[0]).lower() for row in cur.fetchall()}

            if "attempt_count" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN attempt_count INT NOT NULL DEFAULT 0"
                )
            if "next_retry_at" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN next_retry_at DATETIME NULL"
                )
            if "locked_at" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN locked_at DATETIME NULL"
                )
            if "lock_owner" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN lock_owner VARCHAR(80) NULL"
                )
            if "dead_at" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN dead_at DATETIME NULL"
                )
            if "dead_reason" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN dead_reason TEXT NULL"
                )
            if "provider_message_sid" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN provider_message_sid VARCHAR(80) NULL"
                )
            if "delivery_status" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN delivery_status VARCHAR(30) NULL"
                )
            if "delivery_updated_at" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN delivery_updated_at DATETIME NULL"
                )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS message_delivery_status (
                    delivery_id BIGINT NOT NULL AUTO_INCREMENT,
                    provider VARCHAR(30) NOT NULL,
                    provider_message_sid VARCHAR(100) NOT NULL,
                    channel VARCHAR(30) NOT NULL,
                    message_status VARCHAR(40) NOT NULL,
                    to_number VARCHAR(120) NULL,
                    from_number VARCHAR(120) NULL,
                    error_code VARCHAR(40) NULL,
                    error_message TEXT NULL,
                    payload_json TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (delivery_id),
                    UNIQUE KEY uq_provider_sid (provider, provider_message_sid),
                    KEY idx_delivery_status (message_status, updated_at)
                ) ENGINE=InnoDB
                """
            )
            # ── doctor_remainder_queue: add lead_minutes if missing ──────────
            cur.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doctor_remainder_queue'"
            )
            if cur.fetchone()[0]:
                cur.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doctor_remainder_queue'"
                )
                drq_cols = {str(r[0]).lower() for r in cur.fetchall()}
                if "lead_minutes" not in drq_cols:
                    cur.execute(
                        "ALTER TABLE doctor_remainder_queue "
                        "ADD COLUMN lead_minutes INT NOT NULL DEFAULT 10"
                    )

            conn.commit()
            self._invalidate_table_columns_cache(
                appointment_table,
                "appointment_notification_log",
                "message_delivery_status",
                "doctor_remainder_queue",
                "patients",
                "doctors",
            )
        finally:
            cur.close()
            conn.close()

    def log_notification_event(
        self,
        *,
        appointment_id: int,
        event_type: str,
        channel: str,
        destination: str = "",
        status: str = "PENDING",
        error_text: str = "",
        admin_id: Optional[int] = None,
        meta_json: str = "",
    ) -> None:
        if not appointment_id:
            return
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO appointment_notification_log
                (appointment_id, event_type, channel, destination, status, error_text, meta_json, admin_id, sent_at, attempt_count, next_retry_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'SENT' THEN CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30') ELSE NULL END, 0, NULL)
                """,
                (
                    appointment_id,
                    (event_type or "").strip().upper(),
                    (channel or "").strip().lower() or "system",
                    (destination or "").strip(),
                    (status or "").strip().upper() or "PENDING",
                    (error_text or "").strip() or None,
                    (meta_json or "").strip() or None,
                    admin_id,
                    (status or "").strip().upper() or "PENDING",
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def log_doctor_delayed_notification(
        self,
        *,
        appointment_id: int,
        channel: str,
        destination: str = "",
        status: str = "PENDING",
        error_text: str = "",
        admin_id: Optional[int] = None,
        meta_json: str = "",
    ) -> None:
        self.log_notification_event(
            appointment_id=appointment_id,
            event_type="DOCTOR_DELAYED",
            channel=channel,
            destination=destination,
            status=status,
            error_text=error_text,
            admin_id=admin_id,
            meta_json=meta_json,
        )

    def list_pending_notification_events(
        self,
        *,
        limit: int = 200,
        admin_id: Optional[int] = None,
    ) -> list[NotificationEvent]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            safe_limit = max(1, min(1000, int(limit)))
            appointment_table = self._appointment_table()
            chat_col = self._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
            chat_select = f"COALESCE(p.{chat_col}, '')" if chat_col else "''"

            params: list[object] = []
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND l.admin_id = %s"
                params.append(admin_id)

            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT
                        l.notification_id,
                        l.appointment_id,
                        l.event_type,
                        COALESCE(l.channel, '') AS channel,
                        COALESCE(l.destination, '') AS destination,
                        COALESCE(l.status, 'PENDING') AS status,
                        COALESCE(l.attempt_count, 0) AS attempt_count,
                        COALESCE(l.meta_json, '') AS meta_json,
                        l.admin_id,
                        COALESCE(p.full_name, '') AS patient_name,
                        COALESCE(c.clinic_name, '') AS clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone,
                        {chat_select} AS patient_telegram_chat_id
                    FROM appointment_notification_log l
                    JOIN {appointment_table} a ON a.appointment_id = l.appointment_id
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE l.status = 'PENDING'
                      AND l.dead_at IS NULL
                      AND (l.next_retry_at IS NULL OR l.next_retry_at <= CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'))
                      AND l.event_type IN ('CANCELLED', 'RESCHEDULED', 'DOCTOR_DELAYED')
                      {admin_sql}
                    ORDER BY l.notification_id
                    LIMIT {safe_limit}
                    """,
                    tuple(params),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        l.notification_id,
                        l.appointment_id,
                        l.event_type,
                        COALESCE(l.channel, '') AS channel,
                        COALESCE(l.destination, '') AS destination,
                        COALESCE(l.status, 'PENDING') AS status,
                        COALESCE(l.attempt_count, 0) AS attempt_count,
                        COALESCE(l.meta_json, '') AS meta_json,
                        l.admin_id,
                        COALESCE(p.full_name, '') AS patient_name,
                        COALESCE(c.clinic_name, '') AS clinic_name,
                        DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone,
                        {chat_select} AS patient_telegram_chat_id
                    FROM appointment_notification_log l
                    JOIN {appointment_table} a ON a.appointment_id = l.appointment_id
                    LEFT JOIN slots s ON s.slot_id = a.slot_id
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE l.status = 'PENDING'
                      AND l.dead_at IS NULL
                      AND (l.next_retry_at IS NULL OR l.next_retry_at <= CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'))
                      AND l.event_type IN ('CANCELLED', 'RESCHEDULED', 'DOCTOR_DELAYED')
                      {admin_sql}
                    ORDER BY l.notification_id
                    LIMIT {safe_limit}
                    """,
                    tuple(params),
                )

            rows = cur.fetchall()
            events: list[NotificationEvent] = []
            for row in rows:
                events.append(
                    NotificationEvent(
                        notification_id=int(row["notification_id"]),
                        appointment_id=int(row["appointment_id"]),
                        event_type=str(row.get("event_type") or "").strip().upper(),
                        channel=str(row.get("channel") or "").strip().lower(),
                        destination=str(row.get("destination") or "").strip(),
                        status=str(row.get("status") or "PENDING").strip().upper(),
                        patient_name=str(row.get("patient_name") or ""),
                        clinic_name=str(row.get("clinic_name") or ""),
                        slot_date=str(row.get("slot_date") or ""),
                        slot_time=str(row.get("slot_time") or ""),
                        patient_phone=str(row.get("patient_phone") or ""),
                        patient_telegram_chat_id=str(row.get("patient_telegram_chat_id") or ""),
                        meta_json=str(row.get("meta_json") or ""),
                        admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                        attempt_count=int(row.get("attempt_count") or 0),
                    )
                )
            return events
        finally:
            cur.close()
            conn.close()

    def mark_notification_event_status(
        self,
        *,
        notification_id: int,
        status: str,
        error_text: str = "",
        provider_message_sid: str = "",
    ) -> None:
        if not notification_id:
            return
        normalized = (status or "").strip().upper() or "FAILED"
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE appointment_notification_log
                SET status = %s,
                    error_text = %s,
                    provider_message_sid = CASE WHEN %s = 'SENT' AND %s <> '' THEN %s ELSE provider_message_sid END,
                    sent_at = CASE WHEN %s = 'SENT' THEN CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30') ELSE sent_at END,
                    locked_at = NULL,
                    lock_owner = NULL
                WHERE notification_id = %s
                """,
                (
                    normalized,
                    (error_text or "").strip() or None,
                    normalized,
                    (provider_message_sid or "").strip(),
                    (provider_message_sid or "").strip(),
                    normalized,
                    notification_id,
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def claim_pending_notification_events(
        self,
        *,
        limit: int = 100,
        worker_id: str,
        admin_id: Optional[int] = None,
    ) -> list[NotificationEvent]:
        conn = self._connect()
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            safe_limit = max(1, min(500, int(limit)))
            params: list[object] = []
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND l.admin_id = %s"
                params.append(admin_id)

            try:
                cur.execute(
                    f"""
                    SELECT l.notification_id
                    FROM appointment_notification_log l
                    WHERE l.status IN ('PENDING', 'FAILED')
                      AND l.dead_at IS NULL
                      AND (l.next_retry_at IS NULL OR l.next_retry_at <= CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'))
                      AND (l.locked_at IS NULL OR l.locked_at < DATE_SUB(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'), INTERVAL 5 MINUTE))
                      {admin_sql}
                    ORDER BY l.notification_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    tuple(params + [safe_limit]),
                )
            except Exception:
                cur.execute(
                    f"""
                    SELECT l.notification_id
                    FROM appointment_notification_log l
                    WHERE l.status IN ('PENDING', 'FAILED')
                      AND l.dead_at IS NULL
                      AND (l.next_retry_at IS NULL OR l.next_retry_at <= CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'))
                      AND (l.locked_at IS NULL OR l.locked_at < DATE_SUB(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'), INTERVAL 5 MINUTE))
                      {admin_sql}
                    ORDER BY l.notification_id
                    LIMIT %s
                    FOR UPDATE
                    """,
                    tuple(params + [safe_limit]),
                )
            rows = cur.fetchall()
            ids = [int(row["notification_id"]) for row in rows]
            if not ids:
                conn.commit()
                return []

            placeholders = ", ".join(["%s"] * len(ids))
            cur.execute(
                f"""
                UPDATE appointment_notification_log
                SET status = 'PROCESSING',
                    attempt_count = attempt_count + 1,
                    locked_at = CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'),
                    lock_owner = %s
                WHERE notification_id IN ({placeholders})
                """,
                tuple([worker_id] + ids),
            )

            appointment_table = self._appointment_table()
            chat_col = self._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
            chat_select = f"COALESCE(p.{chat_col}, '')" if chat_col else "''"

            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT
                        l.notification_id,
                        l.appointment_id,
                        l.event_type,
                        COALESCE(l.channel, '') AS channel,
                        COALESCE(l.destination, '') AS destination,
                        COALESCE(l.status, 'PENDING') AS status,
                        COALESCE(l.attempt_count, 0) AS attempt_count,
                        COALESCE(l.meta_json, '') AS meta_json,
                        l.admin_id,
                        COALESCE(p.full_name, '') AS patient_name,
                        COALESCE(c.clinic_name, '') AS clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone,
                        {chat_select} AS patient_telegram_chat_id
                    FROM appointment_notification_log l
                    JOIN {appointment_table} a ON a.appointment_id = l.appointment_id
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE l.notification_id IN ({placeholders})
                    ORDER BY l.notification_id
                    """,
                    tuple(ids),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        l.notification_id,
                        l.appointment_id,
                        l.event_type,
                        COALESCE(l.channel, '') AS channel,
                        COALESCE(l.destination, '') AS destination,
                        COALESCE(l.status, 'PENDING') AS status,
                        COALESCE(l.attempt_count, 0) AS attempt_count,
                        COALESCE(l.meta_json, '') AS meta_json,
                        l.admin_id,
                        COALESCE(p.full_name, '') AS patient_name,
                        COALESCE(c.clinic_name, '') AS clinic_name,
                        DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone,
                        {chat_select} AS patient_telegram_chat_id
                    FROM appointment_notification_log l
                    JOIN {appointment_table} a ON a.appointment_id = l.appointment_id
                    LEFT JOIN slots s ON s.slot_id = a.slot_id
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE l.notification_id IN ({placeholders})
                    ORDER BY l.notification_id
                    """,
                    tuple(ids),
                )
            rows = cur.fetchall()
            events: list[NotificationEvent] = []
            for row in rows:
                notification_id = row.get("notification_id")
                appointment_id = row.get("appointment_id")
                if notification_id is None or appointment_id is None:
                    continue
                events.append(
                    NotificationEvent(
                        notification_id=int(notification_id),
                        appointment_id=int(appointment_id),
                        event_type=str(row.get("event_type") or "").strip().upper(),
                        channel=str(row.get("channel") or "").strip().lower(),
                        destination=str(row.get("destination") or "").strip(),
                        status=str(row.get("status") or "PENDING").strip().upper(),
                        patient_name=str(row.get("patient_name") or ""),
                        clinic_name=str(row.get("clinic_name") or ""),
                        slot_date=str(row.get("slot_date") or ""),
                        slot_time=str(row.get("slot_time") or ""),
                        patient_phone=str(row.get("patient_phone") or ""),
                        patient_telegram_chat_id=str(row.get("patient_telegram_chat_id") or ""),
                        meta_json=str(row.get("meta_json") or ""),
                        admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                        attempt_count=int(row.get("attempt_count") or 0),
                    )
                )
            conn.commit()
            return events
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def mark_notification_event_retry(
        self,
        *,
        notification_id: int,
        error_text: str,
        backoff_seconds: int,
        max_attempts: int,
    ) -> None:
        if not notification_id:
            return
        conn = self._connect()
        cur = conn.cursor()
        try:
            safe_backoff = max(1, int(backoff_seconds))
            safe_max_attempts = max(1, int(max_attempts))
            cur.execute(
                """
                UPDATE appointment_notification_log
                SET
                    status = CASE WHEN attempt_count >= %s THEN 'DEAD' ELSE 'FAILED' END,
                    error_text = %s,
                    next_retry_at = CASE WHEN attempt_count >= %s THEN next_retry_at ELSE DATE_ADD(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30'), INTERVAL %s SECOND) END,
                    dead_at = CASE WHEN attempt_count >= %s THEN CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30') ELSE dead_at END,
                    dead_reason = CASE WHEN attempt_count >= %s THEN %s ELSE dead_reason END,
                    locked_at = NULL,
                    lock_owner = NULL
                WHERE notification_id = %s
                """,
                (
                    safe_max_attempts,
                    (error_text or "").strip() or None,
                    safe_max_attempts,
                    safe_backoff,
                    safe_max_attempts,
                    safe_max_attempts,
                    (error_text or "").strip() or "max attempts exceeded",
                    int(notification_id),
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def upsert_delivery_status(
        self,
        *,
        provider: str,
        provider_message_sid: str,
        channel: str,
        message_status: str,
        to_number: str = "",
        from_number: str = "",
        error_code: str = "",
        error_message: str = "",
        payload_json: str = "",
    ) -> None:
        sid = (provider_message_sid or "").strip()
        if not sid:
            return
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO message_delivery_status
                (provider, provider_message_sid, channel, message_status, to_number, from_number, error_code, error_message, payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    message_status = VALUES(message_status),
                    to_number = VALUES(to_number),
                    from_number = VALUES(from_number),
                    error_code = VALUES(error_code),
                    error_message = VALUES(error_message),
                    payload_json = VALUES(payload_json),
                    updated_at = CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')
                """,
                (
                    (provider or "").strip().lower() or "unknown",
                    sid,
                    (channel or "").strip().lower() or "unknown",
                    (message_status or "").strip().upper() or "UNKNOWN",
                    (to_number or "").strip() or None,
                    (from_number or "").strip() or None,
                    (error_code or "").strip() or None,
                    (error_message or "").strip() or None,
                    (payload_json or "").strip() or None,
                ),
            )
            cur.execute(
                """
                UPDATE appointment_notification_log
                SET delivery_status = %s,
                    delivery_updated_at = CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')
                WHERE provider_message_sid = %s
                """,
                (
                    (message_status or "").strip().upper() or "UNKNOWN",
                    sid,
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def notification_queue_stats(self) -> dict:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('PENDING', 'FAILED', 'PROCESSING') AND dead_at IS NULL THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'DEAD' OR dead_at IS NOT NULL THEN 1 ELSE 0 END) AS dead
                FROM appointment_notification_log
                """
            )
            row = cur.fetchone() or {}
            return {
                "queued": int(row.get("queued") or 0),
                "dead": int(row.get("dead") or 0),
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _normalize_chat_user_id(value: str) -> str:
        raw = (value or "").strip()
        if raw.startswith("telegram:"):
            raw = raw[len("telegram:") :].strip()
        return raw

    @staticmethod
    def _parse_time_value(raw: object) -> Optional[time]:
        if raw is None:
            return None
        if isinstance(raw, time):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        return None

    @staticmethod
    def _normalize_phone(value: str) -> str:
        raw = (value or "").strip().lower()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :]
        return "".join(ch for ch in raw if ch.isdigit())

    @staticmethod
    def _is_actionable_booking_row(slot_date_raw: object, slot_time_raw: object) -> bool:
        """Treat past bookings as non-active for conversational existing-booking checks."""
        slot_date_text = str(slot_date_raw or "").strip()
        if not slot_date_text:
            return True
        try:
            slot_date_val = datetime.strptime(slot_date_text, "%Y-%m-%d").date()
        except ValueError:
            return True

        today = datetime.now(_IST).date()
        if slot_date_val > today:
            return True
        if slot_date_val < today:
            return False

        slot_time_val = BookingRepository._parse_time_value(slot_time_raw)
        if slot_time_val is None:
            return True
        now_time = datetime.now(_IST).time().replace(second=0, microsecond=0)
        return slot_time_val >= now_time

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

    def _compute_slot_position(
        self,
        cur,
        doctor_id: int,
        clinic_id: Optional[int],
        slot_date,
        slot_time,
    ) -> Optional[int]:
        """Return the 1-based position of slot_time in the doctor's schedule for that date,
        based purely on schedule start_time + slot_duration (independent of other bookings)."""
        slot_time_parsed = self._parse_time_value(slot_time)
        if slot_time_parsed is None:
            return None
        params: list = [doctor_id, slot_date, slot_date, slot_date]
        clinic_sql = ""
        if clinic_id is not None:
            clinic_sql = "AND clinic_id = %s"
            params.insert(1, clinic_id)
        cur.execute(
            f"""
            SELECT start_time, end_time, slot_duration
            FROM doctor_clinic_schedule
            WHERE doctor_id = %s
              {clinic_sql}
              AND effective_from <= %s
              AND effective_to >= %s
              AND day_of_week = MOD(WEEKDAY(%s) + 1, 7)
            ORDER BY start_time
            """,
            tuple(params),
        )
        schedules = cur.fetchall()
        if not schedules:
            return None
        normalized = self._normalize_schedules(schedules)
        slot_result = self._session_slot_index(
            requested_start=slot_time_parsed,
            schedules=normalized,
        )
        if not slot_result:
            return None
        _, slot_number = slot_result
        return slot_number

    @staticmethod
    def _normalize_schedules(schedules: list[dict]) -> list[tuple[time, time, int]]:
        normalized: list[tuple[time, time, int]] = []
        for sch in schedules:
            s = BookingRepository._parse_time_value(sch.get("start_time"))
            e = BookingRepository._parse_time_value(sch.get("end_time"))
            d = int(sch.get("slot_duration") or 0)
            if not s or not e or d <= 0:
                continue
            normalized.append((s, e, d))
        normalized.sort(key=lambda item: item[0])
        return normalized

    @staticmethod
    def _session_slot_index(
        *,
        requested_start: time,
        schedules: list[tuple[time, time, int]],
    ) -> Optional[tuple[time, int]]:
        """Return (end_time, slot_number) where slot_number is 1-based within the matched schedule window.
        This is session-window based (not cumulative across the entire day)."""
        req_dt = datetime.combine(date.today(), requested_start)
        for s, e, d in schedules:
            start_dt = datetime.combine(date.today(), s)
            end_dt = datetime.combine(date.today(), e)
            if req_dt < start_dt or req_dt >= end_dt:
                continue
            diff_minutes = int((req_dt - start_dt).total_seconds() // 60)
            if diff_minutes % d != 0:
                return None
            return (req_dt + timedelta(minutes=d)).time(), (diff_minutes // d) + 1
        return None

    def get_daily_queue_number(self, appointment_id: int) -> Optional[int]:
        if not appointment_id:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            appointment_table = self._appointment_table()
            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT doctor_id, clinic_id, appointment_date, start_time
                    FROM {appointment_table}
                    WHERE appointment_id = %s
                    LIMIT 1
                    """,
                    (appointment_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                doctor_id = int(row["doctor_id"]) if row.get("doctor_id") is not None else None
                clinic_id = int(row["clinic_id"]) if row.get("clinic_id") is not None else None
                slot_date = row.get("appointment_date")
                slot_time = row.get("start_time")
                if doctor_id is None or slot_date is None or slot_time is None:
                    return None
                return self._compute_slot_position(cur, doctor_id, clinic_id, slot_date, slot_time)

            cur.execute(
                f"""
                SELECT a.doctor_id, a.clinic_id, s.slot_date, s.slot_time
                FROM {appointment_table} a
                JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.appointment_id = %s
                LIMIT 1
                """,
                (appointment_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            doctor_id = int(row["doctor_id"]) if row.get("doctor_id") is not None else None
            clinic_id = int(row["clinic_id"]) if row.get("clinic_id") is not None else None
            slot_date = row.get("slot_date")
            slot_time = row.get("slot_time")
            if doctor_id is None or slot_date is None or slot_time is None:
                return None
            return self._compute_slot_position(cur, doctor_id, clinic_id, slot_date, slot_time)
        finally:
            cur.close()
            conn.close()

    def get_doctor_display_name(self, doctor_id: Optional[int], admin_id: Optional[int] = None) -> Optional[str]:
        if doctor_id is None:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params: list[object] = [int(doctor_id)]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(doctor_name), ''), 'Doctor') AS doctor_name
                FROM doctors
                WHERE doctor_id = %s
                  {admin_sql}
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if not row:
                return None
            return str(row.get("doctor_name") or "").strip() or None
        finally:
            cur.close()
            conn.close()

    def find_patient_name_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        target = self._normalize_phone(phone_number)
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            params: list[object] = [actual_admin_id]
            doctor_join = ""
            doctor_sql = ""
            if doctor_id is not None:
                doctor_join = "LEFT JOIN doctors d ON d.doctor_id = p.doctor_id"
                doctor_sql = "AND (p.doctor_id = %s OR d.doctor_id = %s)"
                params.extend([doctor_id, doctor_id])

            phone_candidates: list[str] = []

            def _add_candidate(raw_value: str) -> None:
                value = (raw_value or "").strip()
                if not value:
                    return
                if value not in phone_candidates:
                    phone_candidates.append(value)

            _add_candidate(target)
            _add_candidate(f"+{target}")
            _add_candidate(f"whatsapp:+{target}")
            if len(target) == 10:
                _add_candidate(f"91{target}")
                _add_candidate(f"+91{target}")
                _add_candidate(f"whatsapp:+91{target}")
            if len(target) == 12 and target.startswith("91"):
                local10 = target[-10:]
                _add_candidate(local10)
                _add_candidate(f"+{target}")
                _add_candidate(f"+91{local10}")
                _add_candidate(f"whatsapp:+{target}")
                _add_candidate(f"whatsapp:+91{local10}")

            if phone_candidates:
                placeholders = ", ".join(["%s"] * len(phone_candidates))
                cur.execute(
                    f"""
                    SELECT p.full_name
                    FROM patients p
                    {doctor_join}
                    WHERE p.admin_id = %s
                      {doctor_sql}
                      AND COALESCE(p.phone, '') IN ({placeholders})
                    ORDER BY p.patient_id DESC
                    LIMIT 1
                    """,
                    tuple(params + phone_candidates),
                )
                row = cur.fetchone()
                if row:
                    return str(row.get("full_name") or "").strip() or None

            last10 = target[-10:] if len(target) >= 10 else ""
            if not last10:
                return None
            cur.execute(
                f"""
                SELECT p.full_name
                FROM patients p
                {doctor_join}
                WHERE p.admin_id = %s
                  {doctor_sql}
                  AND RIGHT(
                        REPLACE(
                            REPLACE(
                                REPLACE(
                                    REPLACE(LOWER(COALESCE(p.phone, '')), 'whatsapp:', ''),
                                    '+', ''
                                ),
                                '-', ''
                            ),
                            ' ', ''
                        ),
                        10
                  ) = %s
                ORDER BY p.patient_id DESC
                LIMIT 1
                """,
                tuple(params + [last10]),
            )
            row = cur.fetchone()
            if not row:
                return None
            return str(row.get("full_name") or "").strip() or None
        finally:
            cur.close()
            conn.close()

    def find_patient_name_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        """Look up patient full_name using telegram_chat_id stored in the patients table."""
        target = self._normalize_chat_user_id(chat_user_id)
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            chat_col = self._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
            if not chat_col:
                return None
            params: list[object] = [actual_admin_id, target]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND p.doctor_id = %s"
                params.append(doctor_id)
            cur.execute(
                f"""
                SELECT p.full_name
                FROM patients p
                WHERE p.admin_id = %s
                  AND TRIM(COALESCE(p.{chat_col}, '')) = %s
                  {doctor_sql}
                ORDER BY p.patient_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if row:
                return str(row.get("full_name") or "").strip() or None
            return None
        finally:
            cur.close()
            conn.close()

    def find_patient_phone_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        """Return the phone number stored in patients table for a known Telegram patient."""
        target = self._normalize_chat_user_id(chat_user_id)
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            chat_col = self._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
            if not chat_col:
                return None
            params: list[object] = [actual_admin_id, target]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND p.doctor_id = %s"
                params.append(doctor_id)
            cur.execute(
                f"""
                SELECT p.phone
                FROM patients p
                WHERE p.admin_id = %s
                  AND TRIM(COALESCE(p.{chat_col}, '')) = %s
                  {doctor_sql}
                ORDER BY p.patient_id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if row:
                return self._normalize_phone(str(row.get("phone") or "").strip()) or None
            return None
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
            appointment_table = self._appointment_table()
            if self._use_appointment_mode():
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
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
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
                FROM {appointment_table} a
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

    def find_active_appointment_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[dict]:
        target = self._normalize_phone(phone_number)
        if not target:
            return None

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return None
            appointment_table = self._appointment_table()
            phone_expr = self._normalized_phone_sql_expr("p.phone")
            phone_filter_sql = f"AND ({phone_expr} = %s"
            params_phone: list[object] = [target]
            if len(target) >= 10:
                phone_filter_sql += f" OR RIGHT({phone_expr}, 10) = %s"
                params_phone.append(target[-10:])
            phone_filter_sql += ")"
            if self._use_appointment_mode():
                params: list[object] = [actual_admin_id]
                doctor_sql = ""
                if doctor_id is not None:
                    doctor_sql = "AND a.doctor_id = %s"
                    params.append(doctor_id)
                params.extend(params_phone)
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.clinic_id,
                        a.doctor_id,
                        p.booking_id AS booking_number,
                        c.clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE a.admin_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                      {phone_filter_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                for row in rows:
                    if not self._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                        continue
                    return row
                return None

            params: list[object] = [actual_admin_id]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND a.doctor_id = %s"
                params.append(doctor_id)
            params.extend(params_phone)
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    a.clinic_id,
                    a.doctor_id,
                    c.clinic_name,
                    s.slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                    COALESCE(p.phone, '') AS patient_phone
                FROM {appointment_table} a
                JOIN patients p ON p.patient_id = a.patient_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.admin_id = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                  {doctor_sql}
                  {phone_filter_sql}
                ORDER BY a.appointment_id DESC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            for row in rows:
                if not self._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                    continue
                return row
            return None
        finally:
            cur.close()
            conn.close()

    def list_active_appointments_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        target = self._normalize_phone(phone_number)
        if not target:
            return []

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return []
            appointment_table = self._appointment_table()
            phone_expr = self._normalized_phone_sql_expr("p.phone")
            phone_filter_sql = f"AND ({phone_expr} = %s"
            params_phone: list[object] = [target]
            if len(target) >= 10:
                phone_filter_sql += f" OR RIGHT({phone_expr}, 10) = %s"
                params_phone.append(target[-10:])
            phone_filter_sql += ")"
            if self._use_appointment_mode():
                params: list[object] = [actual_admin_id]
                doctor_sql = ""
                if doctor_id is not None:
                    doctor_sql = "AND a.doctor_id = %s"
                    params.append(doctor_id)
                params.extend(params_phone)
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.clinic_id,
                        a.doctor_id,
                        c.clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE a.admin_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                      {phone_filter_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )
            else:
                params = [actual_admin_id]
                doctor_sql = ""
                if doctor_id is not None:
                    doctor_sql = "AND a.doctor_id = %s"
                    params.append(doctor_id)
                params.extend(params_phone)
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.clinic_id,
                        a.doctor_id,
                        p.booking_id AS booking_number,
                        c.clinic_name,
                        s.slot_date,
                        TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    LEFT JOIN slots s ON s.slot_id = a.slot_id
                    WHERE a.admin_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                      {phone_filter_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )

            matched: list[dict] = []
            for row in cur.fetchall():
                if not self._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                    continue
                matched.append(row)
                if len(matched) >= max(1, limit):
                    break
            return matched
        finally:
            cur.close()
            conn.close()

    def list_active_appointments_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        target = self._normalize_chat_user_id(chat_user_id)
        if not target:
            return []

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            actual_admin_id = admin_id or self.default_admin_id()
            if not actual_admin_id:
                return []
            chat_col = self._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
            if not chat_col:
                return []

            appointment_table = self._appointment_table()
            params: list[object] = [actual_admin_id, target]
            doctor_sql = ""
            if doctor_id is not None:
                doctor_sql = "AND a.doctor_id = %s"
                params.append(doctor_id)

            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.clinic_id,
                        a.doctor_id,
                        p.booking_id AS booking_number,
                        c.clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.{chat_col}, '') AS chat_user_value
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE a.admin_id = %s
                      AND TRIM(COALESCE(p.{chat_col}, '')) = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )
            else:
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.clinic_id,
                        a.doctor_id,
                        p.booking_id AS booking_number,
                        c.clinic_name,
                        s.slot_date,
                        TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                        COALESCE(p.{chat_col}, '') AS chat_user_value
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    LEFT JOIN slots s ON s.slot_id = a.slot_id
                    WHERE a.admin_id = %s
                      AND TRIM(COALESCE(p.{chat_col}, '')) = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )

            matched: list[dict] = []
            for row in cur.fetchall():
                if not self._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                    continue
                matched.append(row)
                if len(matched) >= max(1, limit):
                    break
            return matched
        finally:
            cur.close()
            conn.close()

    def cancel_appointment(
        self,
        appointment_id: int,
        admin_id: Optional[int] = None,
        cancelled_by: str = "PATIENT",
    ) -> bool:
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
            appointment_table = self._appointment_table()
            has_cancelled_by = self._column_exists(appointment_table, "cancelled_by")
            if self._use_appointment_mode():
                if has_cancelled_by:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET status = 'CANCELLED',
                            cancelled_by = %s
                        WHERE appointment_id = %s
                          AND admin_id = %s
                          AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                        """,
                        ((cancelled_by or "PATIENT").strip().upper(), appointment_id, actual_admin_id),
                    )
                else:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET status = 'CANCELLED'
                        WHERE appointment_id = %s
                          AND admin_id = %s
                          AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                        """,
                        (appointment_id, actual_admin_id),
                    )
                ok = cur.rowcount > 0
                if ok:
                    conn.commit()
                else:
                    conn.rollback()
                return ok
            cur.execute(
                f"""
                SELECT slot_id
                FROM {appointment_table}
                WHERE appointment_id = %s
                  AND admin_id = %s
                  AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
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
            if has_cancelled_by:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET status = 'CANCELLED',
                        cancelled_by = %s
                    WHERE appointment_id = %s
                      AND admin_id = %s
                      AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                    """,
                    ((cancelled_by or "PATIENT").strip().upper(), appointment_id, actual_admin_id),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET status = 'CANCELLED'
                    WHERE appointment_id = %s
                      AND admin_id = %s
                      AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
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
        new_clinic_id: Optional[int] = None,
        admin_id: Optional[int] = None,
        rescheduled_by: str = "PATIENT",
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
            appointment_table = self._appointment_table()
            has_rescheduled_by = self._column_exists(appointment_table, "rescheduled_by")

            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT appointment_id, clinic_id, doctor_id, patient_id
                    FROM {appointment_table}
                    WHERE appointment_id = %s
                      AND admin_id = %s
                      AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (appointment_id, actual_admin_id),
                )
                current = cur.fetchone()
                if not current:
                    conn.rollback()
                    return BookingResult(False, "Active appointment not found.")

                clinic_id = int(current["clinic_id"])
                doctor_id = int(current["doctor_id"])
                patient_id = int(current["patient_id"])
                target_clinic_id = int(new_clinic_id) if new_clinic_id is not None else clinic_id
                target_doctor_id = doctor_id
                start_time = datetime.strptime(new_time, "%H:%M").time()
                cur.execute(
                    f"""
                    SELECT appointment_id
                    FROM {appointment_table}
                    WHERE doctor_id = %s
                      AND clinic_id = %s
                      AND appointment_date = %s
                      AND start_time = %s
                      AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      AND appointment_id <> %s
                    LIMIT 1
                    """,
                    (target_doctor_id, target_clinic_id, new_date, start_time, appointment_id),
                )
                if cur.fetchone():
                    conn.rollback()
                    return BookingResult(False, "Requested new slot is not available.")

                cur.execute(
                    """
                    SELECT start_time, end_time, slot_duration
                    FROM doctor_clinic_schedule
                    WHERE doctor_id = %s
                      AND clinic_id = %s
                      AND effective_from <= %s
                      AND effective_to >= %s
                      AND day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                    ORDER BY schedule_id
                    """,
                    (target_doctor_id, target_clinic_id, new_date, new_date, new_date),
                )
                schedules = cur.fetchall()
                valid = False
                end_time = start_time
                requested_slot_number: Optional[int] = None
                normalized_schedules = self._normalize_schedules(schedules)
                slot_result = self._session_slot_index(
                    requested_start=start_time,
                    schedules=normalized_schedules,
                )
                if slot_result:
                    end_time, requested_slot_number = slot_result
                    valid = True
                if not valid:
                    conn.rollback()
                    return BookingResult(False, "Requested new slot is not available.")

                if has_rescheduled_by:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET appointment_date = %s,
                            start_time = %s,
                            end_time = %s,
                            clinic_id = %s,
                            doctor_id = %s,
                            rescheduled_by = %s
                        WHERE appointment_id = %s
                          AND admin_id = %s
                        """,
                        (
                            new_date,
                            start_time,
                            end_time,
                            target_clinic_id,
                            target_doctor_id,
                            (rescheduled_by or "PATIENT").strip().upper(),
                            appointment_id,
                            actual_admin_id,
                        ),
                    )
                else:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET appointment_date = %s,
                            start_time = %s,
                            end_time = %s,
                            clinic_id = %s,
                            doctor_id = %s
                        WHERE appointment_id = %s
                          AND admin_id = %s
                        """,
                        (new_date, start_time, end_time, target_clinic_id, target_doctor_id, appointment_id, actual_admin_id),
                    )
                if self._column_exists("patients", "booking_id") and requested_slot_number is not None:
                    cur.execute(
                        """
                        UPDATE patients
                        SET booking_id = %s
                        WHERE patient_id = %s
                        """,
                        (requested_slot_number, patient_id),
                    )
                conn.commit()
                return BookingResult(
                    True,
                    "Appointment rescheduled.",
                    appointment_id=appointment_id,
                    queue_number=requested_slot_number if requested_slot_number is not None else self.get_daily_queue_number(appointment_id),
                )

            cur.execute(
                f"""
                SELECT appointment_id, slot_id, clinic_id, doctor_id
                FROM {appointment_table}
                WHERE appointment_id = %s
                  AND admin_id = %s
                  AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
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
            target_clinic_id = int(new_clinic_id) if new_clinic_id is not None else clinic_id
            target_doctor_id = doctor_id

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
                (target_clinic_id, target_doctor_id, actual_admin_id, new_date, new_time),
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
            if has_rescheduled_by:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET slot_id = %s,
                        doctor_id = %s,
                        clinic_id = %s,
                        rescheduled_by = %s
                    WHERE appointment_id = %s
                      AND admin_id = %s
                    """,
                    (
                        new_slot_id,
                        new_doctor_id,
                        new_clinic_id,
                        (rescheduled_by or "PATIENT").strip().upper(),
                        appointment_id,
                        actual_admin_id,
                    ),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET slot_id = %s,
                        doctor_id = %s,
                        clinic_id = %s
                    WHERE appointment_id = %s
                      AND admin_id = %s
                    """,
                    (new_slot_id, new_doctor_id, new_clinic_id, appointment_id, actual_admin_id),
                )
            conn.commit()
            return BookingResult(
                True,
                "Appointment rescheduled.",
                appointment_id=appointment_id,
                queue_number=self.get_daily_queue_number(appointment_id),
            )
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

            # Build patient upsert dynamically so schema changes do not break booking save.
            patient_columns = self._table_columns("patients")
            mode_column: Optional[str] = None
            if "mode" in patient_columns:
                mode_column = "mode"
            elif "symptoms" in patient_columns:
                mode_column = "symptoms"
            mode_value = (
                getattr(context, "appointment_mode", None)
                or getattr(context, "symptoms", None)
            )
            reason_value = getattr(context, "reason", None) or "General"

            patient_values: dict[str, object] = {}
            booking_for_self = getattr(context, "booking_for_self", None)
            if "full_name" in patient_columns:
                patient_values["full_name"] = context.patient_name
            if "admin_id" in patient_columns:
                patient_values["admin_id"] = actual_admin_id
            if "phone" in patient_columns:
                patient_values["phone"] = context.phone_number
            chat_user_id_value = self._normalize_chat_user_id(
                str(getattr(context, "chat_user_id", "") or "")
            )
            chat_column: Optional[str] = None
            for candidate in ("telegram_chat_id", "telegram_user_id", "user_id"):
                if candidate in patient_columns:
                    chat_column = candidate
                    break
            if chat_column and chat_user_id_value and booking_for_self is True:
                patient_values[chat_column] = chat_user_id_value
            if "age" in patient_columns:
                patient_values["age"] = context.age
            if "gender" in patient_columns:
                patient_values["gender"] = context.gender
            if "patient_type" in patient_columns:
                patient_values["patient_type"] = context.patient_type
            if "reason" in patient_columns:
                patient_values["reason"] = reason_value
            if mode_column:
                patient_values[mode_column] = mode_value

            # Prefer chat-id match for Telegram self-booking to avoid duplicate-key conflicts
            # on patients.telegram_chat_id when name/phone differ from historical data.
            patient_id: Optional[int] = None
            if chat_column and chat_user_id_value and booking_for_self is True:
                cur.execute(
                    f"""
                    SELECT patient_id
                    FROM patients
                    WHERE admin_id = %s
                      AND TRIM(COALESCE({chat_column}, '')) = %s
                    ORDER BY patient_id DESC
                    LIMIT 1
                    """,
                    (actual_admin_id, chat_user_id_value),
                )
                by_chat = cur.fetchone()
                if by_chat:
                    patient_id = int(by_chat["patient_id"])

            # Fallback lookup by name/admin, and phone when present.
            if patient_id is None:
                lookup_sql = (
                    "SELECT patient_id "
                    "FROM patients "
                    "WHERE full_name = %s AND admin_id = %s"
                )
                lookup_params: list[object] = [context.patient_name, actual_admin_id]
                normalized_phone = self._normalize_phone(str(context.phone_number or ""))
                if normalized_phone:
                    lookup_sql += " AND REPLACE(REPLACE(REPLACE(COALESCE(phone,''), ' ', ''), '-', ''), '+', '') LIKE %s"
                    lookup_params.append(f"%{normalized_phone[-10:]}")
                lookup_sql += " ORDER BY patient_id LIMIT 1"
                cur.execute(lookup_sql, tuple(lookup_params))
                patient_row = cur.fetchone()
                if patient_row:
                    patient_id = int(patient_row["patient_id"])

            update_columns = [
                col
                for col in ("phone", "age", "gender", "patient_type", "reason", mode_column, chat_column)
                if col and col in patient_values
            ]

            def _update_patient_from_values(target_patient_id: int) -> None:
                if not update_columns:
                    return
                assignments = ", ".join(f"{col} = %s" for col in update_columns)
                update_params = [patient_values[col] for col in update_columns]
                update_params.append(target_patient_id)
                cur.execute(
                    f"""
                    UPDATE patients
                    SET {assignments}
                    WHERE patient_id = %s
                    """,
                    tuple(update_params),
                )

            if patient_id is not None:
                _update_patient_from_values(patient_id)
            else:
                insert_columns = [
                    col
                    for col in ("full_name", "admin_id", "phone", "age", "gender", "patient_type", "reason", mode_column, chat_column)
                    if col and col in patient_values
                ]
                if "full_name" not in insert_columns or "admin_id" not in insert_columns:
                    conn.rollback()
                    return BookingResult(False, "Patients schema missing required columns.")
                placeholders = ", ".join(["%s"] * len(insert_columns))
                col_sql = ", ".join(insert_columns)
                params = [patient_values[col] for col in insert_columns]
                try:
                    cur.execute(
                        f"""
                        INSERT INTO patients
                        ({col_sql})
                        VALUES ({placeholders})
                        """,
                        tuple(params),
                    )
                    patient_id = int(cur.lastrowid)
                except Exception as _dup_exc:
                    # If it's NOT a duplicate-key error, re-raise so the outer handler catches it.
                    if "1062" not in str(_dup_exc) and "Duplicate entry" not in str(_dup_exc):
                        raise
                    # Duplicate key on telegram_chat_id (or phone) — recover by fetching existing row.
                    _recovered = False
                    admin_col_exists = "admin_id" in patient_columns
                    select_cols = "patient_id, admin_id" if admin_col_exists else "patient_id"
                    if chat_column and chat_user_id_value:
                        # Prefer same-admin chat-id match.
                        cur.execute(
                            f"""
                            SELECT {select_cols} FROM patients
                            WHERE admin_id = %s
                              AND TRIM(COALESCE({chat_column}, '')) = %s
                            ORDER BY patient_id DESC LIMIT 1
                            """,
                            (actual_admin_id, chat_user_id_value),
                        )
                        _dup_row = cur.fetchone()
                        if _dup_row:
                            patient_id = int(_dup_row["patient_id"])
                            _recovered = True

                    if not _recovered and chat_column and chat_user_id_value:
                        # Detect conflicting cross-admin ownership instead of silently linking.
                        cur.execute(
                            f"""
                            SELECT {select_cols} FROM patients
                            WHERE TRIM(COALESCE({chat_column}, '')) = %s
                            ORDER BY patient_id DESC LIMIT 1
                            """,
                            (chat_user_id_value,),
                        )
                        _dup_row = cur.fetchone()
                        if _dup_row:
                            if admin_col_exists:
                                row_admin_id = _dup_row.get("admin_id")
                                if row_admin_id is not None and int(row_admin_id) != int(actual_admin_id):
                                    conn.rollback()
                                    return BookingResult(
                                        False,
                                        "Telegram chat id is linked to a different admin profile.",
                                    )
                            patient_id = int(_dup_row["patient_id"])
                            _recovered = True
                    if not _recovered:
                        # Try fallback by phone
                        _norm = self._normalize_phone(str(context.phone_number or ""))
                        if _norm:
                            cur.execute(
                                """
                                SELECT patient_id FROM patients
                                WHERE admin_id = %s
                                  AND REPLACE(REPLACE(REPLACE(COALESCE(phone,''),' ',''),'-',''),'+','') LIKE %s
                                ORDER BY patient_id DESC LIMIT 1
                                """,
                                (actual_admin_id, f"%{_norm[-10:]}"),
                            )
                            _dup_row = cur.fetchone()
                            if _dup_row:
                                patient_id = int(_dup_row["patient_id"])
                                _recovered = True
                    if not _recovered:
                        raise
                    _update_patient_from_values(int(patient_id))

            appointment_table = self._appointment_table()
            has_notify_chat_col = self._column_exists(appointment_table, "notify_telegram_chat_id")

            if self._use_appointment_mode():
                resolved_doctor_id = int(doctor_id) if doctor_id is not None else None
                if resolved_doctor_id is None:
                    cur.execute(
                        """
                        SELECT dcs.doctor_id
                        FROM doctor_clinic_schedule dcs
                        WHERE dcs.clinic_id = %s
                          AND dcs.effective_from <= %s
                          AND dcs.effective_to >= %s
                          AND dcs.day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                        ORDER BY dcs.schedule_id
                        LIMIT 1
                        """,
                        (
                            int(context.clinic_id),
                            context.appointment_date,
                            context.appointment_date,
                            context.appointment_date,
                        ),
                    )
                    row = cur.fetchone()
                    resolved_doctor_id = int(row["doctor_id"]) if row and row.get("doctor_id") else None
                if resolved_doctor_id is None:
                    conn.rollback()
                    return BookingResult(False, "Doctor mapping not found for clinic.")
                if "doctor_id" in patient_columns and resolved_doctor_id is not None:
                    cur.execute(
                        """
                        UPDATE patients
                        SET doctor_id = %s
                        WHERE patient_id = %s
                        """,
                        (resolved_doctor_id, patient_id),
                    )

                requested_start = datetime.strptime(str(context.appointment_time), "%H:%M").time()
                cur.execute(
                    """
                    SELECT start_time, end_time, slot_duration
                    FROM doctor_clinic_schedule
                    WHERE doctor_id = %s
                      AND clinic_id = %s
                      AND effective_from <= %s
                      AND effective_to >= %s
                      AND day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                    ORDER BY schedule_id
                    """,
                    (
                        resolved_doctor_id,
                        int(context.clinic_id),
                        context.appointment_date,
                        context.appointment_date,
                        context.appointment_date,
                    ),
                )
                schedules = cur.fetchall()
                matched = False
                requested_end = requested_start
                requested_slot_number: Optional[int] = None
                normalized_schedules = self._normalize_schedules(schedules)
                slot_result = self._session_slot_index(
                    requested_start=requested_start,
                    schedules=normalized_schedules,
                )
                if slot_result:
                    requested_end, requested_slot_number = slot_result
                    matched = True
                if not matched:
                    conn.rollback()
                    return BookingResult(False, "Selected slot is not available.")

                cur.execute(
                    f"""
                    SELECT appointment_id
                    FROM {appointment_table}
                    WHERE patient_id = %s
                      AND clinic_id = %s
                      AND admin_id = %s
                      AND doctor_id = %s
                      AND appointment_date = %s
                      AND start_time = %s
                      AND status = 'BOOKED'
                    ORDER BY appointment_id DESC
                    LIMIT 1
                    """,
                    (
                        patient_id,
                        int(context.clinic_id),
                        actual_admin_id,
                        resolved_doctor_id,
                        context.appointment_date,
                        requested_start,
                    ),
                )
                existing = cur.fetchone()
                if existing:
                    if "booking_id" in patient_columns and requested_slot_number is not None:
                        cur.execute(
                            """
                            UPDATE patients
                            SET booking_id = %s
                            WHERE patient_id = %s
                            """,
                            (requested_slot_number, patient_id),
                        )
                    conn.commit()
                    appt_id = int(existing["appointment_id"])
                    return BookingResult(
                        True,
                        "Appointment already exists.",
                        appointment_id=appt_id,
                        queue_number=requested_slot_number if requested_slot_number is not None else self.get_daily_queue_number(appt_id),
                    )

                # Handle unique key (doctor_id, appointment_date, start_time):
                # if an old cancelled/completed row exists for same slot, reuse it.
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
                    (
                        resolved_doctor_id,
                        context.appointment_date,
                        requested_start,
                    ),
                )
                slot_owner = cur.fetchone()
                if slot_owner:
                    slot_status = str(slot_owner.get("status") or "").upper()
                    slot_appointment_id = int(slot_owner["appointment_id"])
                    if slot_status in {"CANCELLED", "COMPLETED"}:
                        if has_notify_chat_col:
                            cur.execute(
                                f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    notify_telegram_chat_id = %s,
                                    status = 'BOOKED'
                                WHERE appointment_id = %s
                                """,
                                (
                                    patient_id,
                                    int(context.clinic_id),
                                    actual_admin_id,
                                    requested_end,
                                    chat_user_id_value or None,
                                    slot_appointment_id,
                                ),
                            )
                        else:
                            cur.execute(
                                f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    status = 'BOOKED'
                                WHERE appointment_id = %s
                                """,
                                (
                                    patient_id,
                                    int(context.clinic_id),
                                    actual_admin_id,
                                    requested_end,
                                    slot_appointment_id,
                                ),
                            )
                        if "booking_id" in patient_columns and requested_slot_number is not None:
                            cur.execute(
                                """
                                UPDATE patients
                                SET booking_id = %s
                                WHERE patient_id = %s
                                """,
                                (requested_slot_number, patient_id),
                            )
                        conn.commit()
                        return BookingResult(
                            True,
                            "Appointment persisted.",
                            appointment_id=slot_appointment_id,
                            queue_number=requested_slot_number if requested_slot_number is not None else self.get_daily_queue_number(slot_appointment_id),
                        )
                    conn.rollback()
                    return BookingResult(False, "Selected slot is not available.")

                try:
                    if has_notify_chat_col:
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time, notify_telegram_chat_id)
                            VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s, %s)
                            """,
                            (
                                patient_id,
                                resolved_doctor_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                context.appointment_date,
                                requested_start,
                                requested_end,
                                chat_user_id_value or None,
                            ),
                        )
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time)
                            VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s)
                            """,
                            (
                                patient_id,
                                resolved_doctor_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                context.appointment_date,
                                requested_start,
                                requested_end,
                            ),
                        )
                except Exception as _insert_exc:
                    if "1062" not in str(_insert_exc) and "Duplicate entry" not in str(_insert_exc):
                        raise
                    conn.rollback()
                    return BookingResult(False, "Selected slot is not available.")
                appointment_id = int(cur.lastrowid)
                if "booking_id" in patient_columns and requested_slot_number is not None:
                    cur.execute(
                        """
                        UPDATE patients
                        SET booking_id = %s
                        WHERE patient_id = %s
                        """,
                        (requested_slot_number, patient_id),
                    )
                conn.commit()
                return BookingResult(
                    True,
                    "Appointment persisted.",
                    appointment_id=appointment_id,
                    queue_number=requested_slot_number if requested_slot_number is not None else self.get_daily_queue_number(appointment_id),
                )

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
                SELECT a.appointment_id, a.doctor_id
                FROM {appointment_table} a
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
                existing_doctor_id = existing.get("doctor_id")
                if "doctor_id" in patient_columns and existing_doctor_id is not None:
                    cur.execute(
                        """
                        UPDATE patients
                        SET doctor_id = %s
                        WHERE patient_id = %s
                        """,
                        (int(existing_doctor_id), patient_id),
                    )
                if has_notify_chat_col and chat_user_id_value:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET notify_telegram_chat_id = COALESCE(NULLIF(TRIM(notify_telegram_chat_id), ''), %s)
                        WHERE appointment_id = %s
                        """,
                        (chat_user_id_value, int(existing["appointment_id"])),
                    )
                conn.commit()
                appt_id = int(existing["appointment_id"])
                return BookingResult(
                    True,
                    "Appointment already exists.",
                    appointment_id=appt_id,
                    queue_number=self.get_daily_queue_number(appt_id),
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
            if "doctor_id" in patient_columns:
                cur.execute(
                    """
                    UPDATE patients
                    SET doctor_id = %s
                    WHERE patient_id = %s
                    """,
                    (doctor_id, patient_id),
                )

            cur.execute(
                """
                UPDATE slots
                SET slot_status = 'BOOKED'
                WHERE slot_id = %s
                """,
                (slot_id,),
            )

            if has_notify_chat_col:
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    (patient_id, slot_id, doctor_id, clinic_id, admin_id, status, notify_telegram_chat_id)
                    VALUES (%s, %s, %s, %s, %s, 'BOOKED', %s)
                    """,
                    (patient_id, slot_id, doctor_id, clinic_id, actual_admin_id, chat_user_id_value or None),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    (patient_id, slot_id, doctor_id, clinic_id, admin_id, status)
                    VALUES (%s, %s, %s, %s, %s, 'BOOKED')
                    """,
                    (patient_id, slot_id, doctor_id, clinic_id, actual_admin_id),
                )
            appointment_id = int(cur.lastrowid)
            conn.commit()
            return BookingResult(
                True,
                "Appointment persisted.",
                appointment_id=appointment_id,
                queue_number=self.get_daily_queue_number(appointment_id),
            )
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
            appointment_table = self._appointment_table()
            if self._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        a.status,
                        p.full_name AS patient_name,
                        c.clinic_name,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time
                    FROM {appointment_table} a
                    LEFT JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE a.appointment_id = %s
                    """,
                    (appointment_id,),
                )
                return cur.fetchone()
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    a.status,
                    p.full_name AS patient_name,
                    c.clinic_name,
                    s.slot_date,
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time
                FROM {appointment_table} a
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
            appointment_table = self._appointment_table()
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
                        p.booking_id AS booking_number,
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
                    p.booking_id AS booking_number,
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

    # ── doctor_remainder_queue helpers ────────────────────────────────────────

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
