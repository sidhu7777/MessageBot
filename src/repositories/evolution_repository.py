from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass(frozen=True)
class EvolutionDoctorContext:
    doctor_id: int
    admin_id: Optional[int]
    clinic_id: Optional[int]
    instance_name: str
    account_identity: str
    doctor_name: str
    clinic_name: str
    slug: str


class EvolutionRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self.config = config

    def _connect(self):
        return connect_mysql(self.config)

    def ensure_schema(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS doctor_evolution_bindings (
                    binding_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    doctor_id INT NOT NULL,
                    clinic_id INT NULL,
                    evolution_instance_name VARCHAR(191) NOT NULL,
                    evolution_account_identity VARCHAR(64) NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_doctor_evolution_instance (evolution_instance_name),
                    UNIQUE KEY uq_doctor_evolution_account (evolution_account_identity),
                    KEY idx_doctor_evolution_doctor (doctor_id),
                    KEY idx_doctor_evolution_status (status)
                )
                """
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _normalize_identity(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :].strip()
        if "@" in raw:
            raw = raw.split("@", 1)[0].strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return digits
        return raw

    def resolve_doctor_context(
        self,
        *,
        instance_name: str = "",
        connected_account: str = "",
    ) -> Optional[EvolutionDoctorContext]:
        instance_name = str(instance_name or "").strip()
        connected_account = str(connected_account or "").strip()
        context = None
        if instance_name:
            context = self._resolve_by_instance(instance_name)
        if context is None and connected_account:
            context = self._resolve_by_connected_account(connected_account)
        return context

    def _resolve_by_instance(self, instance_name: str) -> Optional[EvolutionDoctorContext]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    b.doctor_id,
                    b.clinic_id,
                    b.evolution_instance_name,
                    b.evolution_account_identity,
                    d.admin_id,
                    COALESCE(NULLIF(TRIM(d.doctor_name), ''), 'Doctor') AS doctor_name,
                    COALESCE(NULLIF(TRIM(d.slug), ''), '') AS slug,
                    COALESCE(NULLIF(TRIM(c.clinic_name), ''), '') AS clinic_name
                FROM doctor_evolution_bindings b
                JOIN doctors d ON d.doctor_id = b.doctor_id
                LEFT JOIN clinics c ON c.clinic_id = b.clinic_id
                WHERE b.status = 'ACTIVE'
                  AND b.evolution_instance_name = %s
                LIMIT 1
                """,
                (instance_name,),
            )
            row = cur.fetchone() or {}
            if not row:
                return None
            clinic_name = str(row.get("clinic_name") or "").strip()
            clinic_id = int(row["clinic_id"]) if row.get("clinic_id") is not None else None
            if not clinic_name:
                fallback = self._first_clinic_for_doctor(cur, int(row["doctor_id"]))
                if fallback:
                    clinic_id = clinic_id or fallback[0]
                    clinic_name = fallback[1]
            return EvolutionDoctorContext(
                doctor_id=int(row["doctor_id"]),
                admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                clinic_id=clinic_id,
                instance_name=str(row.get("evolution_instance_name") or "").strip(),
                account_identity=str(row.get("evolution_account_identity") or "").strip(),
                doctor_name=str(row.get("doctor_name") or "Doctor").strip() or "Doctor",
                clinic_name=clinic_name or "Clinic",
                slug=str(row.get("slug") or "").strip(),
            )
        finally:
            cur.close()
            conn.close()

    def _resolve_by_connected_account(self, connected_account: str) -> Optional[EvolutionDoctorContext]:
        normalized = self._normalize_identity(connected_account)
        if not normalized:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT doctor_id, admin_id, doctor_name, slug, phone
                FROM doctors
                WHERE status = 'ACTIVE'
                """
            )
            rows = cur.fetchall() or []
            for row in rows:
                if self._normalize_identity(str(row.get("phone") or "")) != normalized:
                    continue
                clinic = self._first_clinic_for_doctor(cur, int(row["doctor_id"]))
                return EvolutionDoctorContext(
                    doctor_id=int(row["doctor_id"]),
                    admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                    clinic_id=clinic[0] if clinic else None,
                    instance_name="",
                    account_identity=connected_account,
                    doctor_name=str(row.get("doctor_name") or "Doctor").strip() or "Doctor",
                    clinic_name=clinic[1] if clinic else "Clinic",
                    slug=str(row.get("slug") or "").strip(),
                )
            return None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _first_clinic_for_doctor(cur, doctor_id: int) -> Optional[tuple[int, str]]:
        cur.execute(
            """
            SELECT clinic_id, COALESCE(NULLIF(TRIM(clinic_name), ''), 'Clinic') AS clinic_name
            FROM clinics
            WHERE doctor_id = %s
              AND (status = 'ACTIVE' OR status IS NULL)
            ORDER BY clinic_id ASC
            LIMIT 1
            """,
            (int(doctor_id),),
        )
        row = cur.fetchone() or {}
        if not row or row.get("clinic_id") is None:
            return None
        return int(row["clinic_id"]), str(row.get("clinic_name") or "Clinic").strip() or "Clinic"


__all__ = ["EvolutionDoctorContext", "EvolutionRepository"]
