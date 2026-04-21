import json
from dataclasses import dataclass
from datetime import datetime, timedelta, date, time
import threading
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql
from src.timezone_utils import now_in_runtime_timezone
from src.runtime.account_scope import parse_scoped_user_id
from src.repositories.notification_ops import (
    claim_pending_notification_events as _claim_pending_notification_events,
    list_pending_notification_events as _list_pending_notification_events,
    log_notification_event as _log_notification_event,
    mark_notification_event_retry as _mark_notification_event_retry,
    mark_notification_event_status as _mark_notification_event_status,
    notification_queue_stats as _notification_queue_stats,
    upsert_delivery_status as _upsert_delivery_status,
)
from src.repositories.reminder_ops import (
    get_appointment_status as _get_appointment_status,
    get_extra_doctor_contacts as _get_extra_doctor_contacts,
    insert_or_get_reminder_queue as _insert_or_get_reminder_queue,
    is_reminder_sent as _is_reminder_sent,
    list_due_doctor_reminders as _list_due_doctor_reminders,
    mark_reminder_failed as _mark_reminder_failed,
    mark_reminder_sent as _mark_reminder_sent,
)
from src.repositories.notification_repository import NotificationEvent
from src.repositories.reminder_repository import DoctorReminder
from src.repositories.booking_query_ops import (
    find_active_appointment_by_patient_name as _find_active_appointment_by_patient_name,
    find_active_appointment_by_phone_number as _find_active_appointment_by_phone_number,
    find_patient_name_by_chat_user_id as _find_patient_name_by_chat_user_id,
    find_patient_name_by_phone_number as _find_patient_name_by_phone_number,
    find_patient_phone_by_chat_user_id as _find_patient_phone_by_chat_user_id,
    get_doctor_display_name as _get_doctor_display_name,
    list_active_appointments_by_chat_user_id as _list_active_appointments_by_chat_user_id,
    list_active_appointments_by_phone_number as _list_active_appointments_by_phone_number,
)
from src.repositories.booking_write_ops import (
    cancel_appointment as _cancel_appointment,
    reschedule_appointment_same_clinic as _reschedule_appointment_same_clinic,
    save_confirmed_appointment as _save_confirmed_appointment,
)


@dataclass
class BookingResult:
    ok: bool
    message: str
    appointment_id: Optional[int] = None
    queue_number: Optional[int] = None


class BookingRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self.redis_client = None
        self._redis_key_prefix = "msgbot"
        self._meta_cache_lock = threading.Lock()
        self._table_exists_cache: dict[str, bool] = {}
        self._table_columns_cache: dict[str, set[str]] = {}
        self._appointment_table_cache: Optional[str] = None
        self._use_appointment_mode_cache: Optional[bool] = None

    def _connect(self):
        return connect_mysql(self._config)

    def set_redis_client(self, redis_client: Optional[object], *, key_prefix: str = "msgbot") -> None:
        self.redis_client = redis_client
        self._redis_key_prefix = (key_prefix or "msgbot").strip() or "msgbot"

    def _patient_phone_cache_key(self, *, admin_id: Optional[int], doctor_id: Optional[int], phone_number: str) -> str:
        admin_key = str(int(admin_id)) if admin_id is not None else "na"
        doctor_key = str(int(doctor_id)) if doctor_id is not None else "na"
        return f"{self._redis_key_prefix}:patient:phone:{admin_key}:{doctor_key}:{phone_number}"

    def _patient_chat_cache_key(self, *, admin_id: Optional[int], doctor_id: Optional[int], chat_user_id: str) -> str:
        admin_key = str(int(admin_id)) if admin_id is not None else "na"
        doctor_key = str(int(doctor_id)) if doctor_id is not None else "na"
        return f"{self._redis_key_prefix}:patient:chat:{admin_key}:{doctor_key}:{chat_user_id}"

    def _load_cached_patient_identity(self, *, key: str) -> Optional[dict]:
        if not self.redis_client or not key.strip():
            return None
        try:
            raw = self.redis_client.get(key)
            if not raw:
                return None
            payload = json.loads(str(raw))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _save_cached_patient_identity(
        self,
        *,
        key: str,
        full_name: Optional[str],
        phone_number: Optional[str] = None,
        ttl_seconds: int = 1800,
    ) -> None:
        if not self.redis_client or not key.strip():
            return
        existing = self._load_cached_patient_identity(key=key) or {}
        merged_name = str(full_name or "").strip() or str(existing.get("full_name") or "").strip()
        merged_phone = self._normalize_phone(str(phone_number or "").strip()) or self._normalize_phone(
            str(existing.get("phone_number") or "").strip()
        )
        payload = {
            "full_name": merged_name,
            "phone_number": merged_phone or "",
        }
        try:
            self.redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=max(60, int(ttl_seconds)))
        except Exception:
            return

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

    def ensure_appointment_columns(self) -> None:
        """Ensure appointment table has runtime-required audit, booking, and actor columns."""
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
            if "notify_telegram_chat_id" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN notify_telegram_chat_id BIGINT NULL")
            if "booking_id" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN booking_id INT NULL")
            if "booked_for" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN booked_for VARCHAR(10) NULL")
            if "channel" not in cols:
                cur.execute(f"ALTER TABLE {appointment_table} ADD COLUMN channel VARCHAR(30) NULL")

            conn.commit()
            self._invalidate_table_columns_cache(appointment_table)
        finally:
            cur.close()
            conn.close()

    def ensure_notification_schema(self) -> None:
        """Ensure notification tables/columns exist for runtime workers."""
        from src.repositories.notification_repository import NotificationRepository

        NotificationRepository(self._config).ensure_notification_schema()

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
        doctor_id: Optional[int] = None,
        channel_account_id: Optional[int] = None,
        meta_json: str = "",
    ) -> None:
        _log_notification_event(
            self,
            appointment_id=appointment_id,
            event_type=event_type,
            channel=channel,
            destination=destination,
            status=status,
            error_text=error_text,
            admin_id=admin_id,
            doctor_id=doctor_id,
            channel_account_id=channel_account_id,
            meta_json=meta_json,
        )

    def log_doctor_delayed_notification(
        self,
        *,
        appointment_id: int,
        channel: str,
        destination: str = "",
        status: str = "PENDING",
        error_text: str = "",
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        channel_account_id: Optional[int] = None,
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
            doctor_id=doctor_id,
            channel_account_id=channel_account_id,
            meta_json=meta_json,
        )

    def list_pending_notification_events(
        self,
        *,
        limit: int = 200,
        admin_id: Optional[int] = None,
    ) -> list[NotificationEvent]:
        return _list_pending_notification_events(
            self,
            limit=limit,
            admin_id=admin_id,
            notification_event_cls=NotificationEvent,
        )

    def mark_notification_event_status(
        self,
        *,
        notification_id: int,
        status: str,
        error_text: str = "",
        provider_message_sid: str = "",
        channel_account_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        admin_id: Optional[int] = None,
    ) -> None:
        _mark_notification_event_status(
            self,
            notification_id=notification_id,
            status=status,
            error_text=error_text,
            provider_message_sid=provider_message_sid,
            channel_account_id=channel_account_id,
            doctor_id=doctor_id,
            admin_id=admin_id,
        )

    def claim_pending_notification_events(
        self,
        *,
        limit: int = 100,
        worker_id: str,
        admin_id: Optional[int] = None,
    ) -> list[NotificationEvent]:
        return _claim_pending_notification_events(
            self,
            limit=limit,
            worker_id=worker_id,
            admin_id=admin_id,
            notification_event_cls=NotificationEvent,
        )

    def mark_notification_event_retry(
        self,
        *,
        notification_id: int,
        error_text: str,
        backoff_seconds: int,
        max_attempts: int,
    ) -> None:
        _mark_notification_event_retry(
            self,
            notification_id=notification_id,
            error_text=error_text,
            backoff_seconds=backoff_seconds,
            max_attempts=max_attempts,
        )

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
        channel_account_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        admin_id: Optional[int] = None,
    ) -> None:
        _upsert_delivery_status(
            self,
            provider=provider,
            provider_message_sid=provider_message_sid,
            channel=channel,
            message_status=message_status,
            to_number=to_number,
            from_number=from_number,
            error_code=error_code,
            error_message=error_message,
            payload_json=payload_json,
            channel_account_id=channel_account_id,
            doctor_id=doctor_id,
            admin_id=admin_id,
        )

    def notification_queue_stats(self) -> dict:
        return _notification_queue_stats(self)

    @staticmethod
    def _normalize_chat_user_id(value: str) -> str:
        _channel_account_id, raw_user_id = parse_scoped_user_id(value or "")
        raw = (raw_user_id or "").strip()
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

        today = now_in_runtime_timezone().date()
        if slot_date_val > today:
            return True
        if slot_date_val < today:
            return False

        slot_time_val = BookingRepository._parse_time_value(slot_time_raw)
        if slot_time_val is None:
            return True
        now_time = now_in_runtime_timezone().time().replace(second=0, microsecond=0)
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
        return _get_doctor_display_name(self, doctor_id=doctor_id, admin_id=admin_id)

    def find_patient_name_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        return _find_patient_name_by_phone_number(
            self,
            phone_number=phone_number,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )

    def find_patient_name_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        return _find_patient_name_by_chat_user_id(
            self,
            chat_user_id=chat_user_id,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )

    def find_patient_phone_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[str]:
        return _find_patient_phone_by_chat_user_id(
            self,
            chat_user_id=chat_user_id,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )

    def find_active_appointment_by_patient_name(
        self,
        patient_name: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[dict]:
        return _find_active_appointment_by_patient_name(
            self,
            patient_name=patient_name,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )

    def find_active_appointment_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> Optional[dict]:
        return _find_active_appointment_by_phone_number(
            self,
            phone_number=phone_number,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )

    def list_active_appointments_by_phone_number(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        return _list_active_appointments_by_phone_number(
            self,
            phone_number=phone_number,
            admin_id=admin_id,
            doctor_id=doctor_id,
            limit=limit,
        )

    def list_active_appointments_by_chat_user_id(
        self,
        chat_user_id: str,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        return _list_active_appointments_by_chat_user_id(
            self,
            chat_user_id=chat_user_id,
            admin_id=admin_id,
            doctor_id=doctor_id,
            limit=limit,
        )

    def cancel_appointment(
        self,
        appointment_id: int,
        admin_id: Optional[int] = None,
        cancelled_by: str = "PATIENT",
    ) -> bool:
        return _cancel_appointment(
            self,
            appointment_id=appointment_id,
            admin_id=admin_id,
            cancelled_by=cancelled_by,
        )

    def reschedule_appointment_same_clinic(
        self,
        appointment_id: int,
        new_date: str,
        new_time: str,
        new_clinic_id: Optional[int] = None,
        admin_id: Optional[int] = None,
        rescheduled_by: str = "PATIENT",
    ) -> BookingResult:
        return _reschedule_appointment_same_clinic(
            self,
            appointment_id=appointment_id,
            new_date=new_date,
            new_time=new_time,
            new_clinic_id=new_clinic_id,
            admin_id=admin_id,
            rescheduled_by=rescheduled_by,
        )

    def save_confirmed_appointment(
        self,
        context,
        admin_id: Optional[int] = None,
        doctor_id: Optional[int] = None,
    ) -> BookingResult:
        result = _save_confirmed_appointment(
            self,
            context=context,
            admin_id=admin_id,
            doctor_id=doctor_id,
        )
        if result.ok:
            actual_admin_id = admin_id or self.default_admin_id()
            patient_name = str(getattr(context, "patient_name", "") or "").strip()
            phone_number = self._normalize_phone(str(getattr(context, "phone_number", "") or "").strip())
            chat_user_id = self._normalize_chat_user_id(str(getattr(context, "chat_user_id", "") or "").strip())
            if phone_number:
                self._save_cached_patient_identity(
                    key=self._patient_phone_cache_key(
                        admin_id=actual_admin_id,
                        doctor_id=doctor_id,
                        phone_number=phone_number,
                    ),
                    full_name=patient_name,
                    phone_number=phone_number,
                )
            if chat_user_id:
                self._save_cached_patient_identity(
                    key=self._patient_chat_cache_key(
                        admin_id=actual_admin_id,
                        doctor_id=doctor_id,
                        chat_user_id=chat_user_id,
                    ),
                    full_name=patient_name,
                    phone_number=phone_number,
                )
            # Log SMS confirmation notification if SMS is enabled for this channel
            appointment_id = result.appointment_id
            channel_value = str(getattr(context, "booking_channel", "") or "").strip().lower()
            should_log_confirmation = result.ok
            if appointment_id and channel_value and phone_number and should_log_confirmation:
                try:
                    from src.config import load_settings
                    from src.runtime.sms_notification_service import SMSNotificationService
                    import logging

                    logger = logging.getLogger(__name__)
                    sms_service = SMSNotificationService(load_settings(), logger)
                    
                    # Check if SMS is enabled for this channel
                    if not sms_service.is_sms_enabled_for_channel(channel_value):
                        logger.info(
                            "SMS notification not logged: SMS disabled for channel '%s' (appointment_id=%s)",
                            channel_value,
                            appointment_id,
                        )
                        return result
                    
                    # Log the notification
                    logger.info(
                        "Logging SMS confirmation notification: appointment_id=%s channel=%s phone=%s",
                        appointment_id,
                        channel_value,
                        phone_number[:4] + "****" if len(phone_number) > 4 else phone_number,
                    )
                    self.log_notification_event(
                        appointment_id=appointment_id,
                        event_type="CONFIRMATION",
                        channel="sms",
                        destination=phone_number,
                        status="PENDING",
                        admin_id=actual_admin_id,
                        doctor_id=doctor_id,
                    )
                    logger.info("SMS confirmation notification logged successfully: appointment_id=%s", appointment_id)
                except Exception as exc:
                    # Log but don't fail appointment booking if notification logging fails
                    logger.error(
                        "Failed to log SMS confirmation notification: appointment_id=%s error=%s",
                        appointment_id,
                        exc,
                        exc_info=True,
                    )
        return result

    def get_appointment_status(self, appointment_id: int) -> Optional[dict]:
        return _get_appointment_status(self, appointment_id)

    def list_due_doctor_reminders(
        self,
        lookahead_minutes: int = 180,
        admin_id: Optional[int] = None,
    ) -> list[DoctorReminder]:
        return _list_due_doctor_reminders(
            self,
            lookahead_minutes=lookahead_minutes,
            admin_id=admin_id,
            doctor_reminder_cls=DoctorReminder,
        )

    def get_extra_doctor_contacts(self, doctor_ids: list[int]) -> dict[int, list[dict]]:
        return _get_extra_doctor_contacts(self, doctor_ids)

    # ── doctor_remainder_queue helpers ────────────────────────────────────────

    def is_reminder_sent(self, *, dedup_key: str) -> bool:
        return _is_reminder_sent(self, dedup_key=dedup_key)

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
        return _insert_or_get_reminder_queue(
            self,
            doctor_id=doctor_id,
            schedule_id=schedule_id,
            slot_date=slot_date,
            schedule_start_time=schedule_start_time,
            schedule_end_time=schedule_end_time,
            channel=channel,
            destination=destination,
            lead_minutes=lead_minutes,
            dedup_key=dedup_key,
        )

    def mark_reminder_sent(self, *, queue_id: int) -> None:
        _mark_reminder_sent(self, queue_id=queue_id)

    def mark_reminder_failed(self, *, queue_id: int, error: str) -> None:
        _mark_reminder_failed(self, queue_id=queue_id, error=error)
