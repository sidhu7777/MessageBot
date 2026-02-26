from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
import logging

from src.db.connection import MySQLConfig, connect_mysql

LOGGER = logging.getLogger(__name__)

@dataclass
class ClinicOption:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


@dataclass
class ScheduleRebuildRequest:
    schedule_id: int


class SchedulingRepository:
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

    def _use_appointment_mode(self) -> bool:
        return self._table_exists("appointment") and not self._table_exists("slots")

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
                if (datetime.combine(date.today(), end_t) <= datetime.combine(date.today(), start_t)):
                    continue
                windows.append((start_t, end_t, duration))
            return windows
        finally:
            cur.close()
            conn.close()

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

    @staticmethod
    def _normalize_phone(value: str) -> str:
        raw = (value or "").strip().lower()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :]
        return "".join(ch for ch in raw if ch.isdigit())

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
            if admin_id is not None:
                cur.execute(
                    """
                    SELECT doctor_id, COALESCE(phone, '') AS phone
                    FROM doctors
                    WHERE status = 'ACTIVE' AND admin_id = %s
                    ORDER BY doctor_id
                    """,
                    (admin_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT doctor_id, COALESCE(phone, '') AS phone
                    FROM doctors
                    WHERE status = 'ACTIVE'
                    ORDER BY doctor_id
                    """
                )

            rows = cur.fetchall()
            if not rows:
                return None

            # Prefer exact normalized match.
            for row in rows:
                if self._normalize_phone(row["phone"]) == target:
                    return int(row["doctor_id"])

            # Fallback: match by last 10 digits (common local storage format).
            if len(target) >= 10:
                suffix = target[-10:]
                for row in rows:
                    doctor_phone = self._normalize_phone(row["phone"])
                    if len(doctor_phone) >= 10 and doctor_phone[-10:] == suffix:
                        return int(row["doctor_id"])

            return None
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
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'doctors'
                """
            )
            cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
            candidate_cols = ["username", "telegram_username", "telegram_bot_username"]
            username_col = next((c for c in candidate_cols if c in cols), None)
            if not username_col:
                return None

            if admin_id is not None:
                cur.execute(
                    f"""
                    SELECT doctor_id, COALESCE({username_col}, '') AS username_value
                    FROM doctors
                    WHERE status = 'ACTIVE' AND admin_id = %s
                    ORDER BY doctor_id
                    """,
                    (admin_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT doctor_id, COALESCE({username_col}, '') AS username_value
                    FROM doctors
                    WHERE status = 'ACTIVE'
                    ORDER BY doctor_id
                    """
                )
            for row in cur.fetchall():
                value = str(row.get("username_value") or "").strip().lstrip("@").lower()
                if value and value == target:
                    return int(row["doctor_id"])
            return None
        finally:
            cur.close()
            conn.close()

    def doctor_accept_days(self, doctor_id: int, admin_id: Optional[int] = None) -> int:
        """Return doctor booking acceptance window in days ahead.

        Semantics:
        - 0 => today only
        - 1 => today + tomorrow
        """
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'doctors'
                """
            )
            cols = {str(r["COLUMN_NAME"]).lower() for r in cur.fetchall()}
            column = None
            for candidate in ("acceptdays", "accept_days"):
                if candidate in cols:
                    column = candidate
                    break
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
        if self._use_appointment_mode():
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
                options: list[ClinicOption] = []
                for row in rows:
                    options.append(
                        ClinicOption(
                            clinic_id=int(row["clinic_id"]),
                            clinic_name=row["clinic_name"] or "",
                            location=row["location"] or "",
                            today_slots=0,
                        )
                    )
                return options
            finally:
                cur.close()
                conn.close()

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
                SELECT
                    c.clinic_id,
                    c.clinic_name,
                    COALESCE(c.location, '') AS location,
                    SUM(
                        CASE
                            WHEN s.slot_status = 'AVAILABLE' AND s.slot_date = CURDATE() THEN 1
                            ELSE 0
                        END
                    ) AS today_slots
                FROM clinics c
                JOIN doctor_clinic_schedule dcs ON dcs.clinic_id = c.clinic_id
                LEFT JOIN slots s ON s.schedule_id = dcs.schedule_id
                WHERE dcs.doctor_id = %s
                  AND c.status = 'ACTIVE'
                  {admin_sql}
                GROUP BY c.clinic_id, c.clinic_name, c.location
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
                    today_slots=int(row["today_slots"] or 0),
                )
                for row in rows
            ]
        finally:
            cur.close()
            conn.close()

    def list_available_dates(
        self,
        doctor_id: int,
        clinic_id: int,
        admin_id: Optional[int] = None,
        limit: int = 3,
    ) -> list[str]:
        if self._use_appointment_mode():
            today = date.today()
            max_days = max(0, int(limit) - 1)
            available: list[str] = []
            for offset in range(max_days + 1):
                d = (today + timedelta(days=offset)).isoformat()
                times = self.list_available_times(
                    doctor_id=doctor_id,
                    clinic_id=clinic_id,
                    slot_date=d,
                    admin_id=admin_id,
                    limit=1,
                )
                if times:
                    available.append(d)
                if len(available) >= max(1, int(limit)):
                    break
            return available

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params = [doctor_id, clinic_id]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND s.admin_id = %s"
                params.append(admin_id)
            params.append(limit)
            cur.execute(
                f"""
                SELECT DISTINCT s.slot_date
                FROM slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                WHERE dcs.doctor_id = %s
                  AND dcs.clinic_id = %s
                  AND s.slot_status = 'AVAILABLE'
                  AND s.slot_date >= CURDATE()
                  {admin_sql}
                ORDER BY s.slot_date
                LIMIT %s
                """,
                tuple(params),
            )
            return [str(row["slot_date"]) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    def list_available_times(
        self,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        admin_id: Optional[int] = None,
        limit: int = 3,
    ) -> list[str]:
        if self._use_appointment_mode():
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
            # For today's date, hide already elapsed times from user choices.
            today = date.today().isoformat()
            if slot_date == today:
                now_hhmm = datetime.now().strftime("%H:%M")
                free = [t for t in free if t >= now_hhmm]
            return free[: max(1, int(limit))]

        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            params = [doctor_id, clinic_id, slot_date]
            admin_sql = ""
            if admin_id is not None:
                admin_sql = "AND s.admin_id = %s"
                params.append(admin_id)
            params.append(limit)
            cur.execute(
                f"""
                SELECT DISTINCT TIME_FORMAT(s.slot_time, '%H:%i') AS hhmm
                FROM slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                WHERE dcs.doctor_id = %s
                  AND dcs.clinic_id = %s
                  AND s.slot_date = %s
                  AND s.slot_status = 'AVAILABLE'
                  {admin_sql}
                ORDER BY hhmm
                LIMIT %s
                """,
                tuple(params),
            )
            return [row["hhmm"] for row in cur.fetchall() if row.get("hhmm")]
        finally:
            cur.close()
            conn.close()

    def generate_slots_for_schedule(self, schedule_id: int, days_ahead: int) -> None:
        if self._use_appointment_mode():
            return
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.callproc("generate_slots_for_schedule", (schedule_id, days_ahead))
            # Keep tenant consistency: fill admin_id for newly generated rows.
            cur.execute(
                """
                UPDATE slots s
                JOIN doctor_clinic_schedule dcs ON dcs.schedule_id = s.schedule_id
                JOIN doctors d ON d.doctor_id = dcs.doctor_id
                SET s.admin_id = d.admin_id
                WHERE s.schedule_id = %s
                  AND s.admin_id IS NULL
                """,
                (schedule_id,),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

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
        """Best-effort unique guard for generated AVAILABLE rows."""
        if self._use_appointment_mode():
            return False
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                ALTER TABLE slots
                ADD UNIQUE KEY uq_slots_schedule_date_time_status (
                    schedule_id, slot_date, slot_time, slot_status
                )
                """
            )
            conn.commit()
            return True
        except Exception as exc:
            # Index may already exist, or legacy duplicates may block creation.
            conn.rollback()
            LOGGER.debug("Slot dedup unique index not created: %s", exc)
            return False
        finally:
            cur.close()
            conn.close()

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
        if self._use_appointment_mode():
            return 0
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                DELETE FROM slots
                WHERE schedule_id = %s
                  AND slot_status = 'AVAILABLE'
                  AND slot_date >= CURDATE()
                """,
                (schedule_id,),
            )
            deleted = int(cur.rowcount or 0)
            conn.commit()
            return deleted
        finally:
            cur.close()
            conn.close()

    def deduplicate_future_available_slots(self, schedule_id: int, days_ahead: int) -> int:
        if self._use_appointment_mode():
            return 0
        conn = self._connect()
        cur = conn.cursor()
        try:
            horizon_days = max(1, int(days_ahead))
            cur.execute(
                """
                DELETE s1
                FROM slots s1
                JOIN slots s2
                  ON s1.schedule_id = s2.schedule_id
                 AND s1.slot_date = s2.slot_date
                 AND s1.slot_time = s2.slot_time
                 AND s1.slot_id > s2.slot_id
                WHERE s1.schedule_id = %s
                  AND s1.slot_status = 'AVAILABLE'
                  AND s2.slot_status = 'AVAILABLE'
                  AND s1.slot_date >= CURDATE()
                  AND s1.slot_date <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
                """,
                (schedule_id, horizon_days),
            )
            deleted = int(cur.rowcount or 0)
            conn.commit()
            return deleted
        finally:
            cur.close()
            conn.close()

    def deduplicate_all_future_available_slots(self, days_ahead: int) -> int:
        if self._use_appointment_mode():
            return 0
        conn = self._connect()
        cur = conn.cursor()
        try:
            horizon_days = max(1, int(days_ahead))
            cur.execute(
                """
                DELETE s1
                FROM slots s1
                JOIN slots s2
                  ON s1.schedule_id = s2.schedule_id
                 AND s1.slot_date = s2.slot_date
                 AND s1.slot_time = s2.slot_time
                 AND s1.slot_id > s2.slot_id
                WHERE s1.slot_status = 'AVAILABLE'
                  AND s2.slot_status = 'AVAILABLE'
                  AND s1.slot_date >= CURDATE()
                  AND s1.slot_date <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
                """,
                (horizon_days,),
            )
            deleted = int(cur.rowcount or 0)
            conn.commit()
            return deleted
        finally:
            cur.close()
            conn.close()
