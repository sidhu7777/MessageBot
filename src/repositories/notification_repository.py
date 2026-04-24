from dataclasses import dataclass
import threading
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


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
    doctor_id: Optional[int] = None
    channel_account_id: Optional[int] = None
    attempt_count: int = 0
    source_channel: str = ""
    doctor_name: str = ""
    doctor_slug: str = ""


class NotificationRepository:
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

    def ensure_notification_schema(self) -> None:
        """Ensure notification-related schema exists (notification_log, delivery_status)."""
        conn = self._connect()
        cur = conn.cursor()
        try:
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
            if "source_channel" not in log_cols:
                cur.execute(
                    "ALTER TABLE appointment_notification_log ADD COLUMN source_channel VARCHAR(30) NULL"
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
            conn.commit()
            self._invalidate_table_columns_cache(
                "appointment_notification_log",
                "message_delivery_status",
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
                      AND l.event_type IN ('CONFIRMATION', 'CANCELLED', 'RESCHEDULED', 'DOCTOR_DELAYED')
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
                      AND l.event_type IN ('CONFIRMATION', 'CANCELLED', 'RESCHEDULED', 'DOCTOR_DELAYED')
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
