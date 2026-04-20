import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql
from src.timezone_utils import now_in_runtime_timezone


@dataclass
class ClinicOption:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


@dataclass
class ScheduleRebuildRequest:
    schedule_id: int


@dataclass
class CacheInvalidationEvent:
    queue_id: int
    entity_type: str
    doctor_id: Optional[int] = None
    clinic_id: Optional[int] = None
    admin_id: Optional[int] = None
    slot_date: Optional[str] = None
    slot_time: Optional[str] = None
    old_doctor_id: Optional[int] = None
    old_clinic_id: Optional[int] = None
    old_admin_id: Optional[int] = None
    old_slot_date: Optional[str] = None
    old_slot_time: Optional[str] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None


class SchedulingRepository:
    def __init__(
        self,
        config: MySQLConfig,
        redis_client: Optional[object] = None,
        cache_ttl_seconds: int = 3600,
        cache_key_prefix: str = "msgbot",
    ) -> None:
        self._config = config
        self.redis_client = redis_client
        self._cache_ttl_seconds = max(30, int(cache_ttl_seconds))
        self._cache_key_prefix = (cache_key_prefix or "msgbot").strip() or "msgbot"
        self._table_exists_cache: dict[str, bool] = {}
        self._accept_days_col_cache: dict[str, Optional[str]] = {}
        self._doctors_cols_cache: Optional[set] = None
        self._cached_username_col: Optional[str] = ""  # "" = uninitialised

    def _connect(self):
        return connect_mysql(self._config)

    def _table_exists(self, table_name: str) -> bool:
        if table_name in self._table_exists_cache:
            return self._table_exists_cache[table_name]
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
            result = cur.fetchone() is not None
            self._table_exists_cache[table_name] = result
            return result
        finally:
            cur.close()
            conn.close()

    def set_redis_client(self, redis_client: Optional[object]) -> None:
        self.redis_client = redis_client

    def set_cache_config(self, *, ttl_seconds: Optional[int] = None, key_prefix: Optional[str] = None) -> None:
        if ttl_seconds is not None:
            self._cache_ttl_seconds = max(30, int(ttl_seconds))
        if key_prefix is not None:
            self._cache_key_prefix = (key_prefix or "msgbot").strip() or "msgbot"

    @staticmethod
    def _parse_time_value(raw: object) -> Optional[time]:
        if raw is None:
            return None
        if isinstance(raw, time):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        fmts = ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p")
        for fmt in fmts:
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
    def _normalized_phone_sql_expr(column_expr: str) -> str:
        return (
            f"REPLACE(REPLACE(REPLACE(REPLACE(LOWER(COALESCE({column_expr}, '')), 'whatsapp:', ''), '+', ''), '-', ''), ' ', '')"
        )

    @staticmethod
    def _times_from_windows(windows: list[tuple[time, time, int]]) -> list[str]:
        all_times: set[str] = set()
        for start_t, end_t, duration in windows:
            cursor = datetime.combine(date.today(), start_t)
            end_dt = datetime.combine(date.today(), end_t)
            step = timedelta(minutes=duration)
            while cursor < end_dt:
                all_times.add(cursor.strftime("%H:%M"))
                cursor += step
        return sorted(all_times)

    def _availability_cache_key(self, doctor_id: int, admin_id: Optional[int]) -> str:
        today_key = now_in_runtime_timezone().date().isoformat().replace("-", "")
        admin_key = str(int(admin_id)) if admin_id is not None else "na"
        return f"{self._cache_key_prefix}:avail:{admin_key}:{int(doctor_id)}:{today_key}"

    def invalidate_cached_availability(self, doctor_id: int, admin_id: Optional[int]) -> None:
        if not self.redis_client:
            return
        today_key = now_in_runtime_timezone().date().isoformat().replace("-", "")
        doctor = int(doctor_id)
        keys: list[str] = []
        if admin_id is not None:
            keys.append(f"{self._cache_key_prefix}:avail:{int(admin_id)}:{doctor}:{today_key}")
            keys.append(f"{self._cache_key_prefix}:avail:na:{doctor}:{today_key}")
        else:
            pattern = f"{self._cache_key_prefix}:avail:*:{doctor}:{today_key}"
            try:
                for key in self.redis_client.scan_iter(match=pattern, count=50):
                    keys.append(str(key))
            except Exception:
                return
        if not keys:
            return
        try:
            self.redis_client.delete(*list(dict.fromkeys(keys)))
        except Exception:
            return

    @staticmethod
    def _is_occupied_status(status: Optional[str]) -> bool:
        normalized = str(status or "").strip().upper()
        return normalized in {"BOOKED", "PENDING", "CONFIRMED"}

    @staticmethod
    def _normalize_hhmm(value: Optional[str]) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%H:%M").strftime("%H:%M")
        except Exception:
            return None

    def _update_cached_slot_time(
        self,
        *,
        doctor_id: int,
        admin_id: Optional[int],
        clinic_id: int,
        slot_date: str,
        slot_time: str,
        make_available: bool,
    ) -> None:
        payload = self._load_cached_availability(doctor_id=doctor_id, admin_id=admin_id)
        if not payload:
            return
        clinic_key = str(int(clinic_id))
        key = f"{clinic_key}|{slot_date}"
        dates_by_clinic = payload.get("dates_by_clinic") or {}
        times_by_clinic_date = payload.get("times_by_clinic_date") or {}
        dates = list(dates_by_clinic.get(clinic_key) or [])
        times = list(times_by_clinic_date.get(key) or [])
        if make_available:
            # Conservative: only re-add if the date already exists in cached horizon.
            if slot_date not in dates:
                return
            if slot_time not in times:
                times.append(slot_time)
                times.sort()
            times_by_clinic_date[key] = times
        else:
            if slot_time in times:
                times = [t for t in times if t != slot_time]
            if times:
                times_by_clinic_date[key] = times
            else:
                times_by_clinic_date.pop(key, None)
                if slot_date in dates:
                    dates = [d for d in dates if d != slot_date]
                    dates_by_clinic[clinic_key] = dates
        payload["dates_by_clinic"] = dates_by_clinic
        payload["times_by_clinic_date"] = times_by_clinic_date
        self._save_cached_availability(doctor_id=doctor_id, admin_id=admin_id, payload=payload)

    def _load_cached_availability(self, doctor_id: int, admin_id: Optional[int]) -> Optional[dict]:
        if not self.redis_client:
            return None
        key = self._availability_cache_key(doctor_id, admin_id)
        try:
            raw = self.redis_client.get(key)
            if not raw:
                return None
            payload = json.loads(str(raw))
            if not isinstance(payload, dict):
                return None
            return payload
        except Exception:
            return None

    def _save_cached_availability(self, doctor_id: int, admin_id: Optional[int], payload: dict) -> None:
        if not self.redis_client:
            return
        key = self._availability_cache_key(doctor_id, admin_id)
        try:
            self.redis_client.set(
                key,
                json.dumps(payload, ensure_ascii=False),
                ex=self._cache_ttl_seconds,
            )
        except Exception:
            return

    @staticmethod
    def _build_time_end_lookup(windows: list[tuple[time, time, int]]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for start_t, end_t, duration in windows:
            cursor = datetime.combine(date.today(), start_t)
            end_dt = datetime.combine(date.today(), end_t)
            step = timedelta(minutes=duration)
            while cursor < end_dt:
                slot_start = cursor.strftime("%H:%M")
                slot_end = min(cursor + step, end_dt).strftime("%H:%M")
                lookup[slot_start] = slot_end
                cursor += step
        return lookup

    @staticmethod
    def _filter_runtime_visible_times(
        *,
        slot_date: str,
        times: list[str],
        end_times_by_start: Optional[dict[str, str]] = None,
    ) -> list[str]:
        values = [str(t or "").strip() for t in (times or []) if str(t or "").strip()]
        if not values:
            return []
        today_iso = now_in_runtime_timezone().date().isoformat()
        if slot_date != today_iso:
            return values
        now_hhmm = now_in_runtime_timezone().strftime("%H:%M")
        visible: list[str] = []
        for slot_start in values:
            # For same-day booking, only slots that have not started yet should remain visible.
            if slot_start >= now_hhmm:
                visible.append(slot_start)
        return visible

    def _appointment_windows_for_date(
        self,
        *,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        admin_id: Optional[int],
    ) -> list[tuple[time, time, int]]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params: list[object] = [doctor_id, clinic_id, slot_date, slot_date]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND dcs.admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT dcs.start_time, dcs.end_time, dcs.slot_duration
                FROM doctor_clinic_schedule dcs
                WHERE dcs.doctor_id = %s
                  AND dcs.clinic_id = %s
                  AND dcs.effective_from <= %s
                  AND dcs.effective_to >= %s
                  AND dcs.day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                  {admin_sql}
                ORDER BY dcs.schedule_id
                """,
                tuple(params[:4] + [slot_date] + params[4:]),
            )
            rows = cur.fetchall()
            windows: list[tuple[time, time, int]] = []
            for row in rows:
                start_t = self._parse_time_value(row.get("start_time"))
                end_t = self._parse_time_value(row.get("end_time"))
                duration = int(row.get("slot_duration") or 0)
                if not start_t or not end_t or duration <= 0:
                    continue
                if datetime.combine(date.today(), end_t) <= datetime.combine(date.today(), start_t):
                    continue
                windows.append((start_t, end_t, duration))
            return windows
        finally:
            cur.close()
            conn.close()

    def _db_list_clinics_for_doctor(
        self,
        doctor_id: int,
        admin_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[ClinicOption]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params = [doctor_id]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND c.admin_id = %s"
                params.append(admin_id)
            params.append(limit)
            cur.execute(
                f"""
                SELECT DISTINCT
                    c.clinic_id,
                    c.clinic_name,
                    COALESCE(c.location, '') AS location
                FROM clinics c
                JOIN doctor_clinic_schedule dcs ON dcs.clinic_id = c.clinic_id
                WHERE dcs.doctor_id = %s
                  AND c.status = 'ACTIVE'
                  {admin_sql}
                ORDER BY c.clinic_name
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
            return [
                ClinicOption(
                    clinic_id=int(row["clinic_id"]),
                    clinic_name=row["clinic_name"] or "",
                    location=row["location"] or "",
                    today_slots=0,
                )
                for row in rows
            ]
        finally:
            cur.close()
            conn.close()

    def _db_list_available_times_for_date(
        self,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        admin_id: Optional[int] = None,
    ) -> list[str]:
        windows = self._appointment_windows_for_date(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            admin_id=admin_id,
        )
        if not windows:
            return []
        candidate_times = self._times_from_windows(windows)
        if not candidate_times:
            return []

        # ── Doctor leave check ────────────────────────────────────────────────
        # doctor_leaves rows: start_time/end_time are timedelta (MySQL TIME type).
        # NULL both → full-day leave.  Both set → partial-day leave window.
        _lconn = self._connect()
        _lcur = _lconn.cursor(dictionary=True)
        try:
            _lcur.execute(
                "SELECT start_time, end_time FROM doctor_leaves"
                " WHERE doctor_id = %s AND leave_date = %s",
                (int(doctor_id), slot_date),
            )
            _leave_rows = _lcur.fetchall()
        finally:
            _lcur.close()
            _lconn.close()
        if _leave_rows:
            # Any row with NULL start_time → full-day leave → no slots at all
            if any(r["start_time"] is None for r in _leave_rows):
                return []
            # Partial-day: exclude slots that fall inside a leave window
            _leave_windows = [
                (r["start_time"], r["end_time"])
                for r in _leave_rows
                if r["start_time"] is not None and r["end_time"] is not None
            ]
            if _leave_windows:
                def _in_leave_window(t: str) -> bool:
                    h, m = int(t[:2]), int(t[3:5])
                    td = timedelta(hours=h, minutes=m)
                    return any(ws <= td < we for ws, we in _leave_windows)
                candidate_times = [t for t in candidate_times if not _in_leave_window(t)]
            if not candidate_times:
                return []
        # ─────────────────────────────────────────────────────────────────────

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params: list[object] = [doctor_id, clinic_id, slot_date]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND a.admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT DISTINCT TIME_FORMAT(a.start_time, '%H:%i') AS hhmm
                FROM appointment a
                WHERE a.doctor_id = %s
                  AND a.clinic_id = %s
                  AND a.appointment_date = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                  {admin_sql}
                """,
                tuple(params),
            )
            booked = {str(row["hhmm"]) for row in cur.fetchall() if row.get("hhmm")}
        finally:
            cur.close()
            conn.close()

        free = [t for t in candidate_times if t not in booked]
        free = self._filter_runtime_visible_times(
            slot_date=slot_date,
            times=free,
            end_times_by_start=self._build_time_end_lookup(windows),
        )
        return free

    def _build_availability_snapshot(self, doctor_id: int, admin_id: Optional[int], accept_days: int) -> dict:
        day_horizon = max(0, int(accept_days))
        clinics = self._db_list_clinics_for_doctor(doctor_id=doctor_id, admin_id=admin_id, limit=10)
        clinics_payload = [
            {
                "clinic_id": c.clinic_id,
                "clinic_name": c.clinic_name,
                "location": c.location,
                "today_slots": c.today_slots,
            }
            for c in clinics
        ]
        dates_by_clinic: dict[str, list[str]] = {}
        times_by_clinic_date: dict[str, list[str]] = {}
        time_end_by_clinic_date: dict[str, dict[str, str]] = {}

        for clinic in clinics:
            clinic_key = str(clinic.clinic_id)
            clinic_dates: list[str] = []
            for offset in range(day_horizon + 1):
                day_value = (now_in_runtime_timezone().date() + timedelta(days=offset)).isoformat()
                windows = self._appointment_windows_for_date(
                    doctor_id=doctor_id,
                    clinic_id=clinic.clinic_id,
                    slot_date=day_value,
                    admin_id=admin_id,
                )
                if windows:
                    time_end_by_clinic_date[f"{clinic_key}|{day_value}"] = self._build_time_end_lookup(windows)
                times = self._db_list_available_times_for_date(
                    doctor_id=doctor_id,
                    clinic_id=clinic.clinic_id,
                    slot_date=day_value,
                    admin_id=admin_id,
                )
                if not times:
                    continue
                clinic_dates.append(day_value)
                times_by_clinic_date[f"{clinic_key}|{day_value}"] = times
            dates_by_clinic[clinic_key] = clinic_dates

        return {
            "doctor_id": int(doctor_id),
            "admin_id": int(admin_id) if admin_id is not None else None,
            "accept_days": day_horizon,
            "generated_on": now_in_runtime_timezone().date().isoformat(),
            "clinics": clinics_payload,
            "dates_by_clinic": dates_by_clinic,
            "times_by_clinic_date": times_by_clinic_date,
            "time_end_by_clinic_date": time_end_by_clinic_date,
        }

    def _get_availability_snapshot(self, doctor_id: int, admin_id: Optional[int]) -> dict:
        cached = self._load_cached_availability(doctor_id=doctor_id, admin_id=admin_id)
        if cached:
            return cached
        accept_days = self.doctor_accept_days(doctor_id=doctor_id, admin_id=admin_id)
        snapshot = self._build_availability_snapshot(
            doctor_id=doctor_id,
            admin_id=admin_id,
            accept_days=accept_days,
        )
        self._save_cached_availability(doctor_id=doctor_id, admin_id=admin_id, payload=snapshot)
        return snapshot

    def get_availability_snapshot(self, doctor_id: int, admin_id: Optional[int] = None) -> dict:
        """Return availability snapshot with Redis-first behavior and DB fallback."""
        return self._get_availability_snapshot(doctor_id=doctor_id, admin_id=admin_id)

    def _doctor_ids_for_clinic(self, clinic_id: int, admin_id: Optional[int]) -> list[int]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params: list[object] = [int(clinic_id)]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND dcs.admin_id = %s"
                params.append(int(admin_id))
            cur.execute(
                f"""
                SELECT DISTINCT dcs.doctor_id
                FROM doctor_clinic_schedule dcs
                WHERE dcs.clinic_id = %s
                  {admin_sql}
                """,
                tuple(params),
            )
            return [int(r["doctor_id"]) for r in cur.fetchall() if r.get("doctor_id") is not None]
        finally:
            cur.close()
            conn.close()

    def ensure_cache_invalidation_schema(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS doctor_cache_invalidation_queue (
                    queue_id BIGINT NOT NULL AUTO_INCREMENT,
                    entity_type VARCHAR(20) NOT NULL,
                    doctor_id INT NULL,
                    clinic_id INT NULL,
                    admin_id INT NULL,
                    slot_date DATE NULL,
                    slot_time VARCHAR(5) NULL,
                    old_doctor_id INT NULL,
                    old_clinic_id INT NULL,
                    old_admin_id INT NULL,
                    old_slot_date DATE NULL,
                    old_slot_time VARCHAR(5) NULL,
                    old_status VARCHAR(20) NULL,
                    new_status VARCHAR(20) NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    lock_owner VARCHAR(80) NULL,
                    locked_at DATETIME NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (queue_id),
                    KEY idx_dcq_poll (status, queue_id),
                    KEY idx_dcq_doc (doctor_id),
                    KEY idx_dcq_clinic (clinic_id)
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'doctor_cache_invalidation_queue'
                """
            )
            existing_cols = {str(r[0]).lower() for r in cur.fetchall()}
            alter_specs = [
                ("slot_date", "DATE NULL"),
                ("slot_time", "VARCHAR(5) NULL"),
                ("old_doctor_id", "INT NULL"),
                ("old_clinic_id", "INT NULL"),
                ("old_admin_id", "INT NULL"),
                ("old_slot_date", "DATE NULL"),
                ("old_slot_time", "VARCHAR(5) NULL"),
                ("old_status", "VARCHAR(20) NULL"),
                ("new_status", "VARCHAR(20) NULL"),
            ]
            for col, spec in alter_specs:
                if col not in existing_cols:
                    cur.execute(f"ALTER TABLE doctor_cache_invalidation_queue ADD COLUMN {col} {spec}")
            trigger_ddls: dict[str, str] = {
                "trg_doctors_cache_inv_ai": """
                CREATE TRIGGER trg_doctors_cache_inv_ai
                AFTER INSERT ON doctors
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
                VALUES ('DOCTOR', NEW.doctor_id, NEW.admin_id)
                """,
                "trg_doctors_cache_inv_au": """
                CREATE TRIGGER trg_doctors_cache_inv_au
                AFTER UPDATE ON doctors
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
                VALUES ('DOCTOR', NEW.doctor_id, NEW.admin_id)
                """,
                "trg_doctors_cache_inv_ad": """
                CREATE TRIGGER trg_doctors_cache_inv_ad
                AFTER DELETE ON doctors
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
                VALUES ('DOCTOR', OLD.doctor_id, OLD.admin_id)
                """,
                "trg_dcs_cache_inv_ai": """
                CREATE TRIGGER trg_dcs_cache_inv_ai
                AFTER INSERT ON doctor_clinic_schedule
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
                VALUES ('SCHEDULE', NEW.doctor_id, NEW.clinic_id, NEW.admin_id)
                """,
                "trg_dcs_cache_inv_au": """
                CREATE TRIGGER trg_dcs_cache_inv_au
                AFTER UPDATE ON doctor_clinic_schedule
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
                VALUES ('SCHEDULE', NEW.doctor_id, NEW.clinic_id, NEW.admin_id)
                """,
                "trg_dcs_cache_inv_ad": """
                CREATE TRIGGER trg_dcs_cache_inv_ad
                AFTER DELETE ON doctor_clinic_schedule
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
                VALUES ('SCHEDULE', OLD.doctor_id, OLD.clinic_id, OLD.admin_id)
                """,
                "trg_clinics_cache_inv_ai": """
                CREATE TRIGGER trg_clinics_cache_inv_ai
                AFTER INSERT ON clinics
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
                VALUES ('CLINIC', NEW.clinic_id, NEW.admin_id)
                """,
                "trg_clinics_cache_inv_au": """
                CREATE TRIGGER trg_clinics_cache_inv_au
                AFTER UPDATE ON clinics
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
                VALUES ('CLINIC', NEW.clinic_id, NEW.admin_id)
                """,
                "trg_clinics_cache_inv_ad": """
                CREATE TRIGGER trg_clinics_cache_inv_ad
                AFTER DELETE ON clinics
                FOR EACH ROW
                INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
                VALUES ('CLINIC', OLD.clinic_id, OLD.admin_id)
                """,
            }
            if self._table_exists("patients"):
                trigger_ddls.update(
                    {
                        "trg_patients_cache_inv_ai": """
                        CREATE TRIGGER trg_patients_cache_inv_ai
                        AFTER INSERT ON patients
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
                        VALUES ('PATIENT', NEW.doctor_id, NEW.admin_id)
                        """,
                        "trg_patients_cache_inv_au": """
                        CREATE TRIGGER trg_patients_cache_inv_au
                        AFTER UPDATE ON patients
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id, old_doctor_id, old_admin_id)
                        VALUES ('PATIENT', NEW.doctor_id, NEW.admin_id, OLD.doctor_id, OLD.admin_id)
                        """,
                        "trg_patients_cache_inv_ad": """
                        CREATE TRIGGER trg_patients_cache_inv_ad
                        AFTER DELETE ON patients
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id, old_doctor_id, old_admin_id)
                        VALUES ('PATIENT', OLD.doctor_id, OLD.admin_id, OLD.doctor_id, OLD.admin_id)
                        """,
                    }
                )
            if self._table_exists("appointment"):
                trigger_ddls.update(
                    {
                        "trg_appointment_cache_inv_ai": """
                        CREATE TRIGGER trg_appointment_cache_inv_ai
                        AFTER INSERT ON appointment
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(
                            entity_type, doctor_id, clinic_id, admin_id, slot_date, slot_time, new_status
                        )
                        VALUES (
                            'APPOINTMENT', NEW.doctor_id, NEW.clinic_id, NEW.admin_id,
                            NEW.appointment_date, TIME_FORMAT(NEW.start_time, '%H:%i'), NEW.status
                        )
                        """,
                        "trg_appointment_cache_inv_au": """
                        CREATE TRIGGER trg_appointment_cache_inv_au
                        AFTER UPDATE ON appointment
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(
                            entity_type,
                            doctor_id, clinic_id, admin_id, slot_date, slot_time, new_status,
                            old_doctor_id, old_clinic_id, old_admin_id, old_slot_date, old_slot_time, old_status
                        )
                        VALUES (
                            'APPOINTMENT',
                            NEW.doctor_id, NEW.clinic_id, NEW.admin_id, NEW.appointment_date, TIME_FORMAT(NEW.start_time, '%H:%i'), NEW.status,
                            OLD.doctor_id, OLD.clinic_id, OLD.admin_id, OLD.appointment_date, TIME_FORMAT(OLD.start_time, '%H:%i'), OLD.status
                        )
                        """,
                        "trg_appointment_cache_inv_ad": """
                        CREATE TRIGGER trg_appointment_cache_inv_ad
                        AFTER DELETE ON appointment
                        FOR EACH ROW
                        INSERT INTO doctor_cache_invalidation_queue(
                            entity_type,
                            old_doctor_id, old_clinic_id, old_admin_id, old_slot_date, old_slot_time, old_status
                        )
                        VALUES (
                            'APPOINTMENT',
                            OLD.doctor_id, OLD.clinic_id, OLD.admin_id, OLD.appointment_date, TIME_FORMAT(OLD.start_time, '%H:%i'), OLD.status
                        )
                        """,
                    }
                )
            trigger_names = list(trigger_ddls.keys())
            placeholders = ", ".join(["%s"] * len(trigger_names))
            cur.execute(
                f"""
                SELECT TRIGGER_NAME
                FROM INFORMATION_SCHEMA.TRIGGERS
                WHERE TRIGGER_SCHEMA = DATABASE()
                  AND TRIGGER_NAME IN ({placeholders})
                """,
                tuple(trigger_names),
            )
            existing_triggers = {str(r[0]).strip().lower() for r in cur.fetchall()}
            for trigger_name, ddl in trigger_ddls.items():
                if trigger_name.lower() in existing_triggers:
                    continue
                try:
                    cur.execute(ddl)
                except Exception:
                    # Best-effort creation: avoid destructive drop/recreate behavior.
                    continue
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def claim_cache_invalidation_events(self, *, limit: int, worker_id: str) -> list[CacheInvalidationEvent]:
        conn = self._connect()
        try:
            if getattr(conn, "in_transaction", False):
                conn.rollback()
        except Exception:
            pass
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            safe_limit = max(1, min(200, int(limit)))
            rows: list[dict] = []
            ist_now_sql = "CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')"
            try:
                cur.execute(
                    f"""
                    SELECT queue_id, entity_type, doctor_id, clinic_id, admin_id
                         , DATE_FORMAT(slot_date, '%Y-%m-%d') AS slot_date
                         , slot_time
                         , old_doctor_id, old_clinic_id, old_admin_id
                         , DATE_FORMAT(old_slot_date, '%Y-%m-%d') AS old_slot_date
                         , old_slot_time
                         , old_status, new_status
                    FROM doctor_cache_invalidation_queue
                    WHERE status = 'PENDING'
                      AND (locked_at IS NULL OR locked_at < DATE_SUB({ist_now_sql}, INTERVAL 5 MINUTE))
                    ORDER BY queue_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (safe_limit,),
                )
                rows = cur.fetchall()
            except Exception:
                cur.execute(
                    f"""
                    SELECT queue_id, entity_type, doctor_id, clinic_id, admin_id
                         , DATE_FORMAT(slot_date, '%Y-%m-%d') AS slot_date
                         , slot_time
                         , old_doctor_id, old_clinic_id, old_admin_id
                         , DATE_FORMAT(old_slot_date, '%Y-%m-%d') AS old_slot_date
                         , old_slot_time
                         , old_status, new_status
                    FROM doctor_cache_invalidation_queue
                    WHERE status = 'PENDING'
                      AND (locked_at IS NULL OR locked_at < DATE_SUB({ist_now_sql}, INTERVAL 5 MINUTE))
                    ORDER BY queue_id
                    LIMIT %s
                    FOR UPDATE
                    """,
                    (safe_limit,),
                )
                rows = cur.fetchall()
            if not rows:
                conn.commit()
                return []
            ids = [int(r["queue_id"]) for r in rows]
            placeholders = ", ".join(["%s"] * len(ids))
            cur.execute(
                f"""
                UPDATE doctor_cache_invalidation_queue
                SET status = 'PROCESSING',
                    lock_owner = %s,
                    locked_at = {ist_now_sql}
                WHERE queue_id IN ({placeholders})
                """,
                tuple([worker_id] + ids),
            )
            events = [
                CacheInvalidationEvent(
                    queue_id=int(r["queue_id"]),
                    entity_type=str(r.get("entity_type") or ""),
                    doctor_id=int(r["doctor_id"]) if r.get("doctor_id") is not None else None,
                    clinic_id=int(r["clinic_id"]) if r.get("clinic_id") is not None else None,
                    admin_id=int(r["admin_id"]) if r.get("admin_id") is not None else None,
                    slot_date=str(r.get("slot_date") or "") or None,
                    slot_time=str(r.get("slot_time") or "") or None,
                    old_doctor_id=int(r["old_doctor_id"]) if r.get("old_doctor_id") is not None else None,
                    old_clinic_id=int(r["old_clinic_id"]) if r.get("old_clinic_id") is not None else None,
                    old_admin_id=int(r["old_admin_id"]) if r.get("old_admin_id") is not None else None,
                    old_slot_date=str(r.get("old_slot_date") or "") or None,
                    old_slot_time=str(r.get("old_slot_time") or "") or None,
                    old_status=str(r.get("old_status") or "") or None,
                    new_status=str(r.get("new_status") or "") or None,
                )
                for r in rows
            ]
            conn.commit()
            return events
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def mark_cache_invalidation_done(self, queue_id: int) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE doctor_cache_invalidation_queue
                SET status = 'DONE',
                    lock_owner = NULL,
                    locked_at = NULL
                WHERE queue_id = %s
                """,
                (int(queue_id),),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def release_cache_invalidation(self, queue_id: int) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE doctor_cache_invalidation_queue
                SET status = 'PENDING',
                    lock_owner = NULL,
                    locked_at = NULL
                WHERE queue_id = %s
                """,
                (int(queue_id),),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def process_cache_invalidation_event(self, event: CacheInvalidationEvent) -> None:
        entity_type = (event.entity_type or "").strip().upper()
        if entity_type == "APPOINTMENT":
            self._process_appointment_cache_event(event)
            return
        if entity_type == "PATIENT":
            self._invalidate_patient_cache(admin_id=event.admin_id, doctor_id=event.doctor_id)
            if (
                (event.old_admin_id is not None or event.old_doctor_id is not None)
                and (event.old_admin_id != event.admin_id or event.old_doctor_id != event.doctor_id)
            ):
                self._invalidate_patient_cache(admin_id=event.old_admin_id, doctor_id=event.old_doctor_id)
            return
        doctor_ids: list[int] = []
        if event.doctor_id is not None:
            doctor_ids.append(int(event.doctor_id))
        elif event.clinic_id is not None:
            doctor_ids.extend(self._doctor_ids_for_clinic(clinic_id=event.clinic_id, admin_id=event.admin_id))
        seen: set[int] = set()
        for doc_id in doctor_ids:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            self.invalidate_cached_availability(doctor_id=doc_id, admin_id=event.admin_id)

    def _process_appointment_cache_event(self, event: CacheInvalidationEvent) -> None:
        old_time = self._normalize_hhmm(event.old_slot_time)
        new_time = self._normalize_hhmm(event.slot_time)
        if (
            event.old_doctor_id is not None
            and event.old_clinic_id is not None
            and event.old_slot_date
            and old_time
            and self._is_occupied_status(event.old_status)
        ):
            self._update_cached_slot_time(
                doctor_id=int(event.old_doctor_id),
                admin_id=event.old_admin_id,
                clinic_id=int(event.old_clinic_id),
                slot_date=str(event.old_slot_date),
                slot_time=old_time,
                make_available=True,
            )
        if (
            event.doctor_id is not None
            and event.clinic_id is not None
            and event.slot_date
            and new_time
            and self._is_occupied_status(event.new_status)
        ):
            self._update_cached_slot_time(
                doctor_id=int(event.doctor_id),
                admin_id=event.admin_id,
                clinic_id=int(event.clinic_id),
                slot_date=str(event.slot_date),
                slot_time=new_time,
                make_available=False,
            )

    def _invalidate_patient_cache(self, *, admin_id: Optional[int], doctor_id: Optional[int]) -> None:
        if not self.redis_client:
            return
        admin_key = str(int(admin_id)) if admin_id is not None else "na"
        doctor_key = str(int(doctor_id)) if doctor_id is not None else "na"
        patterns = (
            f"{self._cache_key_prefix}:patient:phone:{admin_key}:{doctor_key}:*",
            f"{self._cache_key_prefix}:patient:chat:{admin_key}:{doctor_key}:*",
        )
        keys: list[str] = []
        for pattern in patterns:
            try:
                for key in self.redis_client.scan_iter(match=pattern, count=50):
                    keys.append(str(key))
            except Exception:
                continue
        if not keys:
            return
        try:
            self.redis_client.delete(*list(dict.fromkeys(keys)))
        except Exception:
            return

    def default_doctor_id(self, admin_id: Optional[int] = None) -> Optional[int]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            if admin_id is not None:
                cur.execute(
                    """
                    SELECT doctor_id
                    FROM doctors
                    WHERE status = 'ACTIVE' AND admin_id = %s
                    ORDER BY doctor_id
                    LIMIT 1
                    """,
                    (admin_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT doctor_id
                    FROM doctors
                    WHERE status = 'ACTIVE'
                    ORDER BY doctor_id
                    LIMIT 1
                    """
                )
            row = cur.fetchone()
            return int(row["doctor_id"]) if row else None
        finally:
            cur.close()
            conn.close()

    def default_doctor_id_by_phone(
        self,
        phone_number: str,
        admin_id: Optional[int] = None,
    ) -> Optional[int]:
        target = self._normalize_phone(phone_number)
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            phone_expr = self._normalized_phone_sql_expr("phone")
            params: list[object] = [target]
            where_suffix = ""
            if len(target) >= 10:
                where_suffix = f" OR RIGHT({phone_expr}, 10) = %s"
                params.append(target[-10:])
            if admin_id is not None:
                cur.execute(
                    f"""
                    SELECT doctor_id
                    FROM doctors
                    WHERE status = 'ACTIVE' AND admin_id = %s
                      AND ({phone_expr} = %s{where_suffix})
                    ORDER BY doctor_id
                    LIMIT 1
                    """,
                    tuple([admin_id] + params),
                )
            else:
                cur.execute(
                    f"""
                    SELECT doctor_id
                    FROM doctors
                    WHERE status = 'ACTIVE'
                      AND ({phone_expr} = %s{where_suffix})
                    ORDER BY doctor_id
                    LIMIT 1
                    """
                    ,
                    tuple(params),
                )
            row = cur.fetchone()
            return int(row["doctor_id"]) if row else None
        finally:
            cur.close()
            conn.close()

    def default_doctor_id_by_username(
        self,
        username: str,
        admin_id: Optional[int] = None,
    ) -> Optional[int]:
        target = (username or "").strip().lstrip("@").lower()
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            # Resolve which column holds the username — cache result to avoid repeated
            # INFORMATION_SCHEMA queries on every message.
            if self._cached_username_col == "":  # uninitialised sentinel
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'doctors'
                    """
                )
                cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
                candidate_cols = ["telegram_userid", "username", "telegram_username", "telegram_bot_username"]
                self._cached_username_col = next((c for c in candidate_cols if c in cols), None)
            username_col = self._cached_username_col
            if not username_col:
                return None
            params: list[object] = [target]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT doctor_id
                FROM doctors
                WHERE status = 'ACTIVE'
                  AND TRIM(LEADING '@' FROM LOWER(COALESCE({username_col}, ''))) = %s
                  {admin_sql}
                ORDER BY doctor_id
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return int(row["doctor_id"]) if row else None
        finally:
            cur.close()
            conn.close()

    def default_doctor_id_by_chat_id(
        self,
        chat_id: str,
        admin_id: Optional[int] = None,
    ) -> Optional[int]:
        """Look up doctor_id by Telegram chat_id from doctors table directly."""
        target = (chat_id or "").strip()
        if target.lower().startswith("telegram:"):
            target = target[len("telegram:") :].strip()
        if not target:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params: list[object] = [target]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND d.admin_id = %s"
                params.append(admin_id)
            # Query doctors table directly for matching chat_id
            cur.execute(
                f"""
                SELECT d.doctor_id
                FROM doctors d
                WHERE d.status = 'ACTIVE'
                  AND d.chat_id = %s
                  {admin_sql}
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if row:
                return int(row["doctor_id"])
            return None
        except Exception:
            return None
        finally:
            cur.close()
            conn.close()

    def doctor_accept_days(self, doctor_id: int, admin_id: Optional[int] = None) -> int:
        # Fast path: reuse availability snapshot cached in Redis.
        # Keeps ASK_AVAILABILITY_DATE light and avoids a DB round-trip per turn.
        cached = self._load_cached_availability(doctor_id=doctor_id, admin_id=admin_id)
        if isinstance(cached, dict):
            raw_cached = cached.get("accept_days")
            try:
                if raw_cached is not None:
                    return max(0, int(raw_cached))
            except Exception:
                pass

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            # Resolve which column holds accept_days — cache to avoid repeated
            # INFORMATION_SCHEMA queries on every availability build.
            if "col" not in self._accept_days_col_cache:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'doctors'
                    """
                )
                cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
                resolved: Optional[str] = None
                for candidate in ("acceptdays", "accept_days"):
                    if candidate in cols:
                        resolved = candidate
                        break
                self._accept_days_col_cache["col"] = resolved
            column = self._accept_days_col_cache["col"]
            if not column:
                return 1
            params: list[object] = [doctor_id]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT {column} AS accept_days
                FROM doctors
                WHERE doctor_id = %s
                  {admin_sql}
                LIMIT 1
                """,
                tuple(params),
            )
            row = cur.fetchone()
            raw = row.get("accept_days") if row else None
            try:
                value = int(raw) if raw is not None else 1
            except Exception:
                value = 1
            return max(0, value)
        finally:
            cur.close()
            conn.close()

    def list_clinics_for_doctor(
        self,
        doctor_id: int,
        admin_id: Optional[int] = None,
        limit: int = 10,
    ) -> list[ClinicOption]:
        snapshot = self._get_availability_snapshot(doctor_id=doctor_id, admin_id=admin_id)
        clinics = snapshot.get("clinics") or []
        out: list[ClinicOption] = []
        for row in clinics[: max(1, int(limit))]:
            out.append(
                ClinicOption(
                    clinic_id=int(row.get("clinic_id") or 0),
                    clinic_name=str(row.get("clinic_name") or ""),
                    location=str(row.get("location") or ""),
                    today_slots=int(row.get("today_slots") or 0),
                )
            )
        return out

    def list_available_dates(
        self,
        doctor_id: int,
        clinic_id: int,
        admin_id: Optional[int] = None,
        limit: int = 3,
    ) -> list[str]:
        snapshot = self._get_availability_snapshot(doctor_id=doctor_id, admin_id=admin_id)
        dates_by_clinic = snapshot.get("dates_by_clinic") or {}
        times_by_clinic_date = snapshot.get("times_by_clinic_date") or {}
        time_end_by_clinic_date = snapshot.get("time_end_by_clinic_date") or {}
        clinic_dates = list(dates_by_clinic.get(str(int(clinic_id))) or [])
        if clinic_dates:
            visible_dates: list[str] = []
            for slot_date in clinic_dates:
                key = f"{int(clinic_id)}|{slot_date}"
                visible_times = self._filter_runtime_visible_times(
                    slot_date=slot_date,
                    times=list(times_by_clinic_date.get(key) or []),
                    end_times_by_start=dict(time_end_by_clinic_date.get(key) or {}),
                )
                if visible_times:
                    visible_dates.append(slot_date)
                if len(visible_dates) >= max(1, int(limit)):
                    break
            if visible_dates:
                return visible_dates

        # Fallback to DB for resilience (cache miss/incomplete payload).
        accept_days = self.doctor_accept_days(doctor_id=doctor_id, admin_id=admin_id)
        max_days = max(0, int(accept_days))
        available: list[str] = []
        for offset in range(max_days + 1):
            d = (now_in_runtime_timezone().date() + timedelta(days=offset)).isoformat()
            times = self._db_list_available_times_for_date(
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                slot_date=d,
                admin_id=admin_id,
            )
            if times:
                available.append(d)
            if len(available) >= max(1, int(limit)):
                break
        return available

    def list_available_times(
        self,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        admin_id: Optional[int] = None,
        limit: int = 3,
    ) -> list[str]:
        snapshot = self._get_availability_snapshot(doctor_id=doctor_id, admin_id=admin_id)
        times_map = snapshot.get("times_by_clinic_date") or {}
        time_end_by_clinic_date = snapshot.get("time_end_by_clinic_date") or {}
        cache_key = f"{int(clinic_id)}|{slot_date}"
        cached_times = self._filter_runtime_visible_times(
            slot_date=slot_date,
            times=list(times_map.get(cache_key) or []),
            end_times_by_start=dict(time_end_by_clinic_date.get(cache_key) or {}),
        )
        if cached_times:
            return cached_times[: max(1, int(limit))]

        # Fallback to DB for resilience (cache miss/incomplete payload).
        db_times = self._db_list_available_times_for_date(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            admin_id=admin_id,
        )
        db_times = self._filter_runtime_visible_times(slot_date=slot_date, times=db_times)
        return db_times[: max(1, int(limit))]

    def generate_slots_for_schedule(self, schedule_id: int, days_ahead: int) -> None:
        return

    def list_active_schedule_ids(self, days_ahead: int = 30, admin_id: Optional[int] = None) -> list[int]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            horizon_days = max(1, int(days_ahead))
            params: list[object] = [horizon_days]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND dcs.admin_id = %s"
                params.append(admin_id)
            cur.execute(
                f"""
                SELECT DISTINCT dcs.schedule_id
                FROM doctor_clinic_schedule dcs
                JOIN doctors d ON d.doctor_id = dcs.doctor_id
                JOIN clinics c ON c.clinic_id = dcs.clinic_id
                WHERE d.status = 'ACTIVE'
                  AND c.status = 'ACTIVE'
                  AND dcs.effective_to >= CURDATE()
                  AND dcs.effective_from <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
                  {admin_sql}
                ORDER BY dcs.schedule_id
                """,
                tuple(params),
            )
            return [int(row["schedule_id"]) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    def ensure_rebuild_queue_schema(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_rebuild_queue (
                    schedule_id INT NOT NULL,
                    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (schedule_id)
                ) ENGINE=InnoDB
                """
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def ensure_slot_dedup_index(self) -> bool:
        return False

    def list_pending_schedule_rebuilds(self, limit: int = 50) -> list[ScheduleRebuildRequest]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT schedule_id
                FROM schedule_rebuild_queue
                ORDER BY requested_at ASC, schedule_id ASC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            return [ScheduleRebuildRequest(schedule_id=int(row["schedule_id"])) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    def clear_schedule_rebuild_request(self, schedule_id: int) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM schedule_rebuild_queue WHERE schedule_id = %s", (schedule_id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def cleanup_future_available_slots(self, schedule_id: int) -> int:
        return 0

    def deduplicate_future_available_slots(self, schedule_id: int, days_ahead: int) -> int:
        return 0

    def deduplicate_all_future_available_slots(self, days_ahead: int) -> int:
        return 0
