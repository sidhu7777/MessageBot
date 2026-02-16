from dataclasses import dataclass
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class ClinicOption:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


class SchedulingRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config

    def _connect(self):
        return connect_mysql(self._config)

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

    def list_clinics_for_doctor(
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
