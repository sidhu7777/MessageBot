from dataclasses import dataclass
from datetime import datetime, timedelta, date, time
from typing import Optional

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


class BookingRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config

    def _connect(self):
        return connect_mysql(self._config)

    def _table_exists(self, table_name: str) -> bool:
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
                (table_name,),
            )
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    def _appointment_table(self) -> str:
        return "appointment" if self._table_exists("appointment") else "appointments"

    def _use_appointment_mode(self) -> bool:
        return self._table_exists("appointment") and not self._table_exists("slots")

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
            conn.commit()
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
                (appointment_id, event_type, channel, destination, status, error_text, meta_json, admin_id, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'SENT' THEN DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE) ELSE NULL END)
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

            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'patients'
                """
            )
            patient_cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
            chat_col = next((c for c in ("telegram_chat_id", "telegram_user_id", "user_id") if c in patient_cols), None)
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
                    sent_at = CASE WHEN %s = 'SENT' THEN DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE) ELSE sent_at END
                WHERE notification_id = %s
                """,
                (
                    normalized,
                    (error_text or "").strip() or None,
                    normalized,
                    notification_id,
                ),
            )
            conn.commit()
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
                    SELECT doctor_id, appointment_date, start_time
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
                slot_date = row.get("appointment_date")
                slot_time = row.get("start_time")
                if doctor_id is None or slot_date is None or slot_time is None:
                    return None
                cur.execute(
                    f"""
                    SELECT COUNT(*) AS queue_number
                    FROM {appointment_table}
                    WHERE doctor_id = %s
                      AND appointment_date = %s
                      AND status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      AND start_time <= %s
                    """,
                    (doctor_id, slot_date, slot_time),
                )
                q = cur.fetchone()
                return int(q["queue_number"]) if q and q.get("queue_number") is not None else None

            cur.execute(
                f"""
                SELECT a.doctor_id, s.slot_date, s.slot_time
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
            slot_date = row.get("slot_date")
            slot_time = row.get("slot_time")
            if doctor_id is None or slot_date is None or slot_time is None:
                return None
            cur.execute(
                f"""
                SELECT COUNT(*) AS queue_number
                FROM {appointment_table} a
                JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.doctor_id = %s
                  AND s.slot_date = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                  AND s.slot_time <= %s
                """,
                (doctor_id, slot_date, slot_time),
            )
            q = cur.fetchone()
            return int(q["queue_number"]) if q and q.get("queue_number") is not None else None
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
            cur.execute(
                f"""
                SELECT p.full_name, COALESCE(p.phone, '') AS patient_phone
                FROM patients p
                {doctor_join}
                WHERE p.admin_id = %s
                  {doctor_sql}
                ORDER BY p.patient_id DESC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            for row in rows:
                patient_phone = self._normalize_phone(str(row.get("patient_phone") or ""))
                if not patient_phone:
                    continue
                if patient_phone == target:
                    return str(row.get("full_name") or "").strip() or None
                if len(target) >= 10 and len(patient_phone) >= 10 and patient_phone[-10:] == target[-10:]:
                    return str(row.get("full_name") or "").strip() or None
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
            if self._use_appointment_mode():
                params: list[object] = [actual_admin_id]
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
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                for row in rows:
                    patient_phone = self._normalize_phone(str(row.get("patient_phone") or ""))
                    if not patient_phone:
                        continue
                    if patient_phone == target:
                        return row
                    if len(target) >= 10 and len(patient_phone) >= 10 and patient_phone[-10:] == target[-10:]:
                        return row
                return None

            params: list[object] = [actual_admin_id]
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
                    TIME_FORMAT(s.slot_time, '%H:%i') AS slot_time,
                    COALESCE(p.phone, '') AS patient_phone
                FROM {appointment_table} a
                JOIN patients p ON p.patient_id = a.patient_id
                LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                LEFT JOIN slots s ON s.slot_id = a.slot_id
                WHERE a.admin_id = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                  {doctor_sql}
                ORDER BY a.appointment_id DESC
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            for row in rows:
                patient_phone = self._normalize_phone(str(row.get("patient_phone") or ""))
                if not patient_phone:
                    continue
                if patient_phone == target:
                    return row
                if len(target) >= 10 and len(patient_phone) >= 10 and patient_phone[-10:] == target[-10:]:
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
            if self._use_appointment_mode():
                params: list[object] = [actual_admin_id]
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
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time,
                        COALESCE(p.phone, '') AS patient_phone
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    LEFT JOIN clinics c ON c.clinic_id = a.clinic_id
                    WHERE a.admin_id = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
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
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )

            matched: list[dict] = []
            for row in cur.fetchall():
                patient_phone = self._normalize_phone(str(row.get("patient_phone") or ""))
                if not patient_phone:
                    continue
                if patient_phone == target or (
                    len(target) >= 10 and len(patient_phone) >= 10 and patient_phone[-10:] == target[-10:]
                ):
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

            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'patients'
                """
            )
            cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
            candidate_cols = ["telegram_chat_id", "telegram_user_id", "user_id"]
            chat_col = next((c for c in candidate_cols if c in cols), None)
            if not chat_col:
                return []

            appointment_table = self._appointment_table()
            params: list[object] = [actual_admin_id]
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
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      {doctor_sql}
                    ORDER BY a.appointment_id DESC
                    """,
                    tuple(params),
                )

            matched: list[dict] = []
            for row in cur.fetchall():
                value = self._normalize_chat_user_id(str(row.get("chat_user_value") or ""))
                if value and value == target:
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
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = 'cancelled_by'
                """,
                (appointment_table,),
            )
            has_cancelled_by = cur.fetchone() is not None
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
                    if (cancelled_by or "").strip().upper() == "DOCTOR":
                        try:
                            self.log_notification_event(
                                appointment_id=appointment_id,
                                event_type="CANCELLED",
                                channel="auto",
                                destination="",
                                status="PENDING",
                                admin_id=actual_admin_id,
                            )
                        except Exception:
                            pass
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
            if (cancelled_by or "").strip().upper() == "DOCTOR":
                try:
                    self.log_notification_event(
                        appointment_id=appointment_id,
                        event_type="CANCELLED",
                        channel="auto",
                        destination="",
                        status="PENDING",
                        admin_id=actual_admin_id,
                    )
                except Exception:
                    pass
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
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = 'rescheduled_by'
                """,
                (appointment_table,),
            )
            has_rescheduled_by = cur.fetchone() is not None

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
                normalized_schedules: list[tuple[time, time, int]] = []
                for sch in schedules:
                    s = self._parse_time_value(sch.get("start_time"))
                    e = self._parse_time_value(sch.get("end_time"))
                    d = int(sch.get("slot_duration") or 0)
                    if not s or not e or d <= 0:
                        continue
                    normalized_schedules.append((s, e, d))
                normalized_schedules.sort(key=lambda item: item[0])
                cumulative_slots = 0
                for s, e, d in normalized_schedules:
                    start_dt = datetime.combine(date.today(), s)
                    end_dt = datetime.combine(date.today(), e)
                    req_dt = datetime.combine(date.today(), start_time)
                    total_minutes = int((end_dt - start_dt).total_seconds() // 60)
                    if total_minutes <= 0:
                        continue
                    slots_in_schedule = total_minutes // d
                    if req_dt < start_dt or req_dt >= end_dt:
                        cumulative_slots += slots_in_schedule
                        continue
                    diff_minutes = int((req_dt - start_dt).total_seconds() // 60)
                    if diff_minutes % d != 0:
                        cumulative_slots += slots_in_schedule
                        continue
                    end_time = (req_dt + timedelta(minutes=d)).time()
                    requested_slot_number = cumulative_slots + (diff_minutes // d) + 1
                    valid = True
                    break
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
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'patients'
                      AND COLUMN_NAME = 'booking_id'
                    """
                )
                if cur.fetchone() and requested_slot_number is not None:
                    cur.execute(
                        """
                        UPDATE patients
                        SET booking_id = %s
                        WHERE patient_id = %s
                        """,
                        (requested_slot_number, patient_id),
                    )
                conn.commit()
                if (rescheduled_by or "").strip().upper() == "DOCTOR":
                    try:
                        self.log_notification_event(
                            appointment_id=appointment_id,
                            event_type="RESCHEDULED",
                            channel="auto",
                            destination="",
                            status="PENDING",
                            admin_id=actual_admin_id,
                        )
                    except Exception:
                        pass
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
            if (rescheduled_by or "").strip().upper() == "DOCTOR":
                try:
                    self.log_notification_event(
                        appointment_id=appointment_id,
                        event_type="RESCHEDULED",
                        channel="auto",
                        destination="",
                        status="PENDING",
                        admin_id=actual_admin_id,
                    )
                except Exception:
                    pass
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
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'patients'
                """
            )
            patient_columns = {str(row["COLUMN_NAME"]).lower() for row in cur.fetchall()}
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
            if chat_column and chat_user_id_value:
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
                update_columns = [
                    col
                    for col in ("phone", "age", "gender", "patient_type", "reason", mode_column, chat_column)
                    if col and col in patient_values
                ]
                if update_columns:
                    assignments = ", ".join(f"{col} = %s" for col in update_columns)
                    params = [patient_values[col] for col in update_columns]
                    params.append(patient_id)
                    cur.execute(
                        f"""
                        UPDATE patients
                        SET {assignments}
                        WHERE patient_id = %s
                        """,
                        tuple(params),
                    )
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
                cur.execute(
                    f"""
                    INSERT INTO patients
                    ({col_sql})
                    VALUES ({placeholders})
                    """,
                    tuple(params),
                )
                patient_id = int(cur.lastrowid)

            appointment_table = self._appointment_table()
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
                normalized_schedules: list[tuple[time, time, int]] = []
                for sch in schedules:
                    s = self._parse_time_value(sch.get("start_time"))
                    e = self._parse_time_value(sch.get("end_time"))
                    d = int(sch.get("slot_duration") or 0)
                    if not s or not e or d <= 0:
                        continue
                    normalized_schedules.append((s, e, d))

                # Daily slot index: cumulative across same-day schedules (sorted by start time).
                normalized_schedules.sort(key=lambda item: item[0])
                cumulative_slots = 0
                for s, e, d in normalized_schedules:
                    start_dt = datetime.combine(date.today(), s)
                    end_dt = datetime.combine(date.today(), e)
                    req_dt = datetime.combine(date.today(), requested_start)
                    total_minutes = int((end_dt - start_dt).total_seconds() // 60)
                    if total_minutes <= 0:
                        continue
                    slots_in_schedule = total_minutes // d
                    if req_dt < start_dt or req_dt >= end_dt:
                        cumulative_slots += slots_in_schedule
                        continue
                    diff_minutes = int((req_dt - start_dt).total_seconds() // 60)
                    if diff_minutes % d != 0:
                        cumulative_slots += slots_in_schedule
                        continue
                    requested_end = (req_dt + timedelta(minutes=d)).time()
                    requested_slot_number = cumulative_slots + (diff_minutes // d) + 1
                    matched = True
                    break
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
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'doctors'
                """
            )
            doctor_columns = {str(row.get("COLUMN_NAME") or "").lower() for row in cur.fetchall()}
            whatsapp_col = "whatsapp_number" if "whatsapp_number" in doctor_columns else None
            telegram_col = None
            for candidate in ("telegram_chat_id", "telegram_user_id", "telegram_id", "chat_id", "user_id"):
                if candidate in doctor_columns:
                    telegram_col = candidate
                    break
            whatsapp_select = f"NULLIF(d.{whatsapp_col}, '')" if whatsapp_col else "NULL"
            telegram_select = f"NULLIF(d.{telegram_col}, '')" if telegram_col else "NULL"
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
                      AND TIMESTAMP(a.appointment_date, a.start_time) >= DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE)
                      AND TIMESTAMP(a.appointment_date, a.start_time) <= DATE_ADD(DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE), INTERVAL %s MINUTE)
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
                    schedule_id = int(row.get("schedule_id") or 0)
                    if schedule_id <= 0:
                        continue
                    results.append(
                        DoctorReminder(
                            appointment_id=int(row["appointment_id"]),
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
                  AND TIMESTAMP(s.slot_date, s.slot_time) >= DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE)
                  AND TIMESTAMP(s.slot_date, s.slot_time) <= DATE_ADD(DATE_ADD(UTC_TIMESTAMP(), INTERVAL 330 MINUTE), INTERVAL %s MINUTE)
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
                schedule_id = int(row.get("schedule_id") or 0)
                if schedule_id <= 0:
                    continue
                results.append(
                    DoctorReminder(
                        appointment_id=int(row["appointment_id"]),
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
