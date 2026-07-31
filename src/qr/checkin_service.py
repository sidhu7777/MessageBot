from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, Optional

from src.messages.templates import get_qr_message
from src.timezone_utils import now_in_runtime_timezone

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


@dataclass
class HospitalQrDoctorOption:
    doctor_id: int
    doctor_name: str
    specialization: str = ""


@dataclass
class HospitalQrScheduleResolution:
    doctor_id: int
    doctor_name: str
    clinic_id: int
    clinic_name: str
    specialization: str = ""


class QrCheckinService:
    def __init__(self, booking_repository: Any, scheduling_repository: Any) -> None:
        self.booking_repository = booking_repository
        self.scheduling_repository = scheduling_repository
        self.qr_overflow_extension_minutes = max(
            0,
            int(os.getenv("QR_OVERFLOW_EXTENSION_MINUTES", "90")),
        )
        # Redis cache-aside for hospital QR options (separate namespace: msgbot:hospital:*).
        # Optional: if no client is set, everything falls back to direct DB reads.
        self.redis_client: Optional[object] = None
        self._cache_key_prefix = "msgbot"
        self._hospital_cache_ttl_seconds = max(
            30,
            int(os.getenv("REDIS_HOSPITAL_CACHE_TTL_SECONDS", "300")),
        )

    def set_redis_client(self, redis_client: Optional[object], *, key_prefix: Optional[str] = None) -> None:
        self.redis_client = redis_client
        if key_prefix is not None:
            self._cache_key_prefix = (key_prefix or "msgbot").strip() or "msgbot"

    def set_cache_config(self, *, ttl_seconds: Optional[int] = None, key_prefix: Optional[str] = None) -> None:
        if ttl_seconds is not None:
            self._hospital_cache_ttl_seconds = max(30, int(ttl_seconds))
        if key_prefix is not None:
            self._cache_key_prefix = (key_prefix or "msgbot").strip() or "msgbot"

    def _hospital_options_cache_key(self, code: str) -> str:
        return f"{self._cache_key_prefix}:hospital:opts:{(code or '').strip()}"

    def _load_cached_hospital_options(self, code: str) -> Optional[dict]:
        if not self.redis_client or not str(code or "").strip():
            return None
        try:
            raw = self.redis_client.get(self._hospital_options_cache_key(code))
            if not raw:
                return None
            payload = json.loads(str(raw))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _save_cached_hospital_options(self, code: str, payload: dict) -> None:
        if not self.redis_client or not str(code or "").strip():
            return
        try:
            self.redis_client.set(
                self._hospital_options_cache_key(code),
                json.dumps(payload, ensure_ascii=False),
                ex=self._hospital_cache_ttl_seconds,
            )
        except Exception:
            return

    def next_registration_sequence(self, hospital_code: str) -> int:
        """Atomic per-day registration counter via Redis INCR (isolated key namespace).

        Key resets daily (TTL). Falls back to a timestamp-based number if Redis is
        unavailable, so a token is always produced. No DB is used.
        """
        code = str(hospital_code or "").strip().lower() or "reg"
        day = now_in_runtime_timezone().strftime("%Y%m%d")
        if self.redis_client:
            try:
                key = f"{self._cache_key_prefix}:registration:seq:{code}:{day}"
                seq = int(self.redis_client.incr(key))
                if seq == 1:
                    try:
                        self.redis_client.expire(key, 172800)  # ~2 days, so it resets each day
                    except Exception:
                        pass
                return seq
            except Exception:
                pass
        # Fallback (Redis down): non-sequential but unique enough within the day.
        return int(now_in_runtime_timezone().strftime("%H%M%S%f"))

    def _resolve_hospital_admin_id(self, hospital_code: str) -> Optional[int]:
        """Admin id for a hospital group (used when no doctor is selected)."""
        code = str(hospital_code or "").strip()
        group_col = self._hospital_group_column()
        if not self.booking_repository or not code or not group_col:
            return None
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT admin_id
                FROM clinics
                WHERE TRIM(COALESCE({group_col}, '')) = %s
                  AND admin_id IS NOT NULL
                ORDER BY clinic_id
                LIMIT 1
                """,
                (code,),
            )
            row = cur.fetchone()
            return int(row["admin_id"]) if row and row.get("admin_id") is not None else None
        finally:
            cur.close()
            conn.close()

    def register_hospital_patient(
        self,
        *,
        hospital_code: str,
        doctor_id: int = 0,
        patient_name: str,
        phone: str,
        age: str = "",
        gender: str = "",
    ) -> dict:
        """Hospital registration (NOT an appointment). Upserts the patients row by
        (admin_id + name + phone) and stores a daily-unique token in `tmpregtoken`.

        - Only name/age/gender/phone are required; doctor is OPTIONAL.
        - Token format: <CODE>/<YYYY>/<MM>/<DD>/<00001> (5-digit daily sequence via Redis).
        - One-per-day: if the matched patient already has TODAY's token, it is returned
          with status 'already_registered' (no new row, no new number).
        - Existing patient (name+phone) -> UPDATE the same row; new patient -> INSERT.
        - Saves hospital_code into patients.hospital_group_code; doctor_id may be NULL.
        """
        if not self.booking_repository:
            return {"status": "error", "message": "Registration database is not configured."}

        cleaned_name = " ".join((patient_name or "").strip().split())
        normalized_phone = self._normalize_phone(phone)
        if not cleaned_name or len(normalized_phone) < 10 or len(normalized_phone) > 15:
            return {"status": "error", "message": "Please enter a valid name and phone number."}

        try:
            doctor_id_val = int(doctor_id) if doctor_id else None
        except Exception:
            doctor_id_val = None

        # admin_id is required (NOT NULL). Prefer the chosen doctor's admin; otherwise
        # resolve it from the hospital code so registration works even with no doctor.
        admin_id = self._resolve_admin_id(doctor_id_val) if doctor_id_val else None
        if not admin_id:
            admin_id = self._resolve_hospital_admin_id(hospital_code)
        if not admin_id:
            return {"status": "error", "message": "This hospital is not configured for registration."}

        try:
            age_val = int(str(age).strip())
        except Exception:
            age_val = None

        now = now_in_runtime_timezone()
        hospital_code_raw = str(hospital_code or "").strip()
        code = re.sub(r"[^A-Za-z0-9]", "", hospital_code_raw).upper() or "REG"
        today_prefix = f"{code}/{now.strftime('%Y/%m/%d')}/"

        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            patient_columns = self.booking_repository._table_columns("patients")
            has_tmp = "tmpregtoken" in patient_columns
            phone_expr = self.booking_repository._normalized_phone_sql_expr("phone")
            profile_type_sql = (
                "AND UPPER(COALESCE(profile_type, 'SELF')) = 'SELF'"
                if "profile_type" in patient_columns
                else ""
            )
            cur.execute(
                f"""
                SELECT patient_id, tmpregtoken
                FROM patients
                WHERE admin_id = %s
                  AND TRIM(LOWER(COALESCE(full_name, ''))) = %s
                  {profile_type_sql}
                  AND ({phone_expr} = %s OR RIGHT({phone_expr}, 10) = %s)
                ORDER BY patient_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (admin_id, cleaned_name.lower(), normalized_phone, normalized_phone[-10:]),
            )
            row = cur.fetchone()

            # One registration per patient per day.
            if row and has_tmp:
                existing = str(row.get("tmpregtoken") or "")
                if existing.startswith(today_prefix):
                    conn.rollback()
                    return {
                        "status": "already_registered",
                        "token": existing,
                        "message": f"You are already registered today. Your token is {existing}.",
                    }

            sequence = self.next_registration_sequence(hospital_code)
            token = f"{today_prefix}{sequence:05d}"

            has_group = "hospital_group_code" in patient_columns

            if row:
                patient_id = int(row["patient_id"])
                set_parts = ["full_name = %s", "phone = %s"]
                set_vals: list[object] = [cleaned_name, normalized_phone]
                if doctor_id_val is not None:  # only overwrite when a doctor was chosen
                    set_parts.append("doctor_id = %s")
                    set_vals.append(doctor_id_val)
                if has_group and hospital_code_raw:
                    set_parts.append("hospital_group_code = %s")
                    set_vals.append(hospital_code_raw)
                if "age" in patient_columns and age_val is not None:
                    set_parts.append("age = %s")
                    set_vals.append(age_val)
                if "gender" in patient_columns and gender:
                    set_parts.append("gender = %s")
                    set_vals.append(gender)
                if has_tmp:
                    set_parts.append("tmpregtoken = %s")
                    set_vals.append(token)
                cur.execute(
                    f"UPDATE patients SET {', '.join(set_parts)} WHERE patient_id = %s",
                    tuple(set_vals + [patient_id]),
                )
            else:
                cols = ["full_name", "admin_id", "phone", "doctor_id"]
                vals: list[object] = [cleaned_name, admin_id, normalized_phone, doctor_id_val]
                if has_group and hospital_code_raw:
                    cols.append("hospital_group_code")
                    vals.append(hospital_code_raw)
                if "profile_type" in patient_columns:
                    cols.append("profile_type")
                    vals.append("SELF")
                if "age" in patient_columns and age_val is not None:
                    cols.append("age")
                    vals.append(age_val)
                if "gender" in patient_columns and gender:
                    cols.append("gender")
                    vals.append(gender)
                if has_tmp:
                    cols.append("tmpregtoken")
                    vals.append(token)
                cur.execute(
                    f"INSERT INTO patients ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    tuple(vals),
                )

            conn.commit()
            return {
                "status": "registered",
                "token": token,
                "message": f"Registration successful. Your token is {token}.",
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _normalize_language(language: str) -> str:
        lang = (language or "").strip().lower()
        return lang if lang in {"en", "hi", "hinglish"} else "en"

    def _msg(self, language: str, key: str, **kwargs: Any) -> str:
        lang = self._normalize_language(language)
        templates = {
            "booking_db_not_configured": {
                "en": "Booking database is not configured.",
                "hi": "बुकिंग डेटाबेस कॉन्फ़िगर नहीं है।",
                "hinglish": "Booking database configure nahi hai.",
            },
            "enter_patient_name": {
                "en": "Please enter patient name.",
                "hi": "कृपया मरीज का नाम दर्ज करें।",
                "hinglish": "Please patient name enter kariye.",
            },
            "enter_valid_phone": {
                "en": "Please enter a valid phone number.",
                "hi": "कृपया सही फोन नंबर दर्ज करें।",
                "hinglish": "Please valid phone number enter kariye.",
            },
            "doctor_admin_not_configured": {
                "en": "Doctor/admin mapping is not configured.",
                "hi": "डॉक्टर/एडमिन मैपिंग कॉन्फ़िगर नहीं है।",
                "hinglish": "Doctor/admin mapping configure nahi hai.",
            },
            "active_booking": {
                "en": "You already have an active booking (#{booking_number}){suffix}",
                "hi": "आपकी पहले से एक सक्रिय बुकिंग (#{booking_number}) है{suffix}",
                "hinglish": "Aapki pehle se ek active booking (#{booking_number}) hai{suffix}",
            },
            "active_booking_suffix": {
                "en": " on {slot_date} {slot_time}.",
                "hi": " {slot_date} {slot_time} पर।",
                "hinglish": " {slot_date} {slot_time} par.",
            },
            "active_booking_suffix_empty": {
                "en": ".",
                "hi": "।",
                "hinglish": ".",
            },
            "appointment_confirmed": {
                "en": "Appointment confirmed. Appointment ID: {booking_number}{suffix}",
                "hi": "अपॉइंटमेंट कन्फर्म हो गई। अपॉइंटमेंट आईडी: {booking_number}{suffix}",
                "hinglish": "Appointment confirm ho gayi. Appointment ID: {booking_number}{suffix}",
            },
            "appointment_confirmed_suffix": {
                "en": " on {slot_date} {slot_time}.",
                "hi": " {slot_date} {slot_time} पर।",
                "hinglish": " {slot_date} {slot_time} par.",
            },
            "appointment_confirmed_suffix_empty": {
                "en": ".",
                "hi": "।",
                "hinglish": ".",
            },
            "unable_confirm_qr": {
                "en": "Unable to confirm QR booking: {error}",
                "hi": "QR बुकिंग कन्फर्म नहीं हो सकी: {error}",
                "hinglish": "QR booking confirm nahi ho saki: {error}",
            },
        }
        template = templates[key][lang]
        return template.format(**kwargs)

    @staticmethod
    def _normalize_phone(value: str) -> str:
        raw = (value or "").strip().lower()
        if raw.startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :]
        return "".join(ch for ch in raw if ch.isdigit())

    @staticmethod
    def _format_time_12h(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
            try:
                parsed = datetime.strptime(text.upper(), fmt)
                return parsed.strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
        return text

    def _hospital_group_column(self) -> Optional[str]:
        if not self.booking_repository:
            return None
        try:
            if self.booking_repository._column_exists("clinics", "hospital_group_code"):
                return "hospital_group_code"
        except Exception:
            return None
        return None

    def _schedule_status_sql(self) -> str:
        if not self.booking_repository:
            return ""
        try:
            if self.booking_repository._column_exists("doctor_clinic_schedule", "status"):
                return "AND UPPER(COALESCE(dcs.status, 'ACTIVE')) = 'ACTIVE'"
        except Exception:
            return ""
        return ""

    def hospital_qr_display_name(self, hospital_code: str) -> str:
        code = str(hospital_code or "").strip()
        group_col = self._hospital_group_column()
        if not self.booking_repository or not code or not group_col:
            return ""
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT COALESCE(NULLIF(TRIM(clinic_name), ''), '') AS clinic_name,
                       COUNT(*) AS clinic_count
                FROM clinics
                WHERE TRIM(COALESCE({group_col}, '')) = %s
                  AND UPPER(COALESCE(status, 'ACTIVE')) = 'ACTIVE'
                GROUP BY clinic_name
                HAVING clinic_name <> ''
                ORDER BY clinic_count DESC, clinic_name
                LIMIT 1
                """,
                (code,),
            )
            row = cur.fetchone()
            return str((row or {}).get("clinic_name") or "").strip()
        finally:
            cur.close()
            conn.close()

    def list_hospital_qr_doctors(self, hospital_code: str) -> list[HospitalQrDoctorOption]:
        """Doctors available today across all clinics in the hospital group."""
        code = str(hospital_code or "").strip()
        group_col = self._hospital_group_column()
        if not self.booking_repository or not code or not group_col:
            return []
        today = now_in_runtime_timezone().date().isoformat()
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT DISTINCT
                    d.doctor_id,
                    COALESCE(NULLIF(TRIM(d.doctor_name), ''), 'Doctor') AS doctor_name,
                    COALESCE(NULLIF(TRIM(d.specialization), ''), '') AS specialization
                FROM clinics c
                JOIN doctor_clinic_schedule dcs ON dcs.clinic_id = c.clinic_id
                JOIN doctors d ON d.doctor_id = dcs.doctor_id
                WHERE TRIM(COALESCE(c.{group_col}, '')) = %s
                  AND UPPER(COALESCE(c.status, 'ACTIVE')) = 'ACTIVE'
                  AND UPPER(COALESCE(d.status, 'ACTIVE')) = 'ACTIVE'
                  AND (d.active_from IS NULL OR d.active_from <= %s)
                  AND (d.active_to IS NULL OR d.active_to >= %s)
                  AND dcs.effective_from <= %s
                  AND dcs.effective_to >= %s
                  AND dcs.day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                  {self._schedule_status_sql()}
                ORDER BY specialization, doctor_name
                """,
                (code, today, today, today, today, today),
            )
            return [
                HospitalQrDoctorOption(
                    doctor_id=int(row["doctor_id"]),
                    doctor_name=str(row.get("doctor_name") or "Doctor"),
                    specialization=str(row.get("specialization") or ""),
                )
                for row in cur.fetchall()
                if row.get("doctor_id") is not None
            ]
        finally:
            cur.close()
            conn.close()

    def hospital_qr_options(self, hospital_code: str) -> dict:
        code = str(hospital_code or "").strip()

        # Cache-aside: try Redis first; on hit return immediately (fast path).
        cached = self._load_cached_hospital_options(code)
        if cached is not None:
            return cached

        # Miss (or Redis unavailable) -> read from DB, then populate the cache.
        doctors = self.list_hospital_qr_doctors(hospital_code)
        display_name = self.hospital_qr_display_name(hospital_code)
        specializations = sorted(
            {
                option.specialization
                for option in doctors
                if str(option.specialization or "").strip()
            },
            key=lambda value: value.lower(),
        )
        result = {
            "hospital_code": code,
            "hospital_name": display_name,
            "specializations": specializations,
            "doctors": [option.__dict__ for option in doctors],
        }
        self._save_cached_hospital_options(code, result)
        return result

    def resolve_hospital_qr_schedule(
        self,
        *,
        hospital_code: str,
        doctor_id: int,
    ) -> Optional[HospitalQrScheduleResolution]:
        """Resolve the clinic from today's valid schedule inside the hospital group."""
        code = str(hospital_code or "").strip()
        group_col = self._hospital_group_column()
        if not self.booking_repository or not code or not doctor_id or not group_col:
            return None
        today = now_in_runtime_timezone().date().isoformat()
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT
                    c.clinic_id,
                    COALESCE(NULLIF(TRIM(c.clinic_name), ''), 'Clinic') AS clinic_name,
                    d.doctor_id,
                    COALESCE(NULLIF(TRIM(d.doctor_name), ''), 'Doctor') AS doctor_name,
                    COALESCE(NULLIF(TRIM(d.specialization), ''), '') AS specialization,
                    dcs.start_time,
                    dcs.end_time,
                    dcs.slot_duration
                FROM clinics c
                JOIN doctor_clinic_schedule dcs ON dcs.clinic_id = c.clinic_id
                JOIN doctors d ON d.doctor_id = dcs.doctor_id
                WHERE TRIM(COALESCE(c.{group_col}, '')) = %s
                  AND d.doctor_id = %s
                  AND UPPER(COALESCE(c.status, 'ACTIVE')) = 'ACTIVE'
                  AND UPPER(COALESCE(d.status, 'ACTIVE')) = 'ACTIVE'
                  AND (d.active_from IS NULL OR d.active_from <= %s)
                  AND (d.active_to IS NULL OR d.active_to >= %s)
                  AND dcs.effective_from <= %s
                  AND dcs.effective_to >= %s
                  AND dcs.day_of_week = MOD(WEEKDAY(%s) + 1, 7)
                  {self._schedule_status_sql()}
                ORDER BY dcs.start_time
                """,
                (code, int(doctor_id), today, today, today, today, today),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()
        if not rows:
            return None

        now_dt = now_in_runtime_timezone().replace(tzinfo=None)
        extension = timedelta(minutes=self.qr_overflow_extension_minutes)

        def row_start_end(row: dict) -> tuple[Optional[time], Optional[time]]:
            return (
                self.booking_repository._parse_time_value(row.get("start_time")),
                self.booking_repository._parse_time_value(row.get("end_time")),
            )

        def rank(row: dict) -> tuple[int, str]:
            start_t, end_t = row_start_end(row)
            if not start_t or not end_t:
                return (4, "")
            start_dt = datetime.combine(now_dt.date(), start_t)
            end_dt = datetime.combine(now_dt.date(), end_t)
            if start_dt <= now_dt < end_dt:
                return (0, start_t.strftime("%H:%M"))
            if end_dt <= now_dt <= end_dt + extension:
                return (1, end_t.strftime("%H:%M"))
            if now_dt < start_dt:
                return (2, start_t.strftime("%H:%M"))
            return (3, start_t.strftime("%H:%M"))

        selected = sorted(rows, key=rank)[0]
        return HospitalQrScheduleResolution(
            doctor_id=int(selected["doctor_id"]),
            doctor_name=str(selected.get("doctor_name") or "Doctor"),
            clinic_id=int(selected["clinic_id"]),
            clinic_name=str(selected.get("clinic_name") or "Clinic"),
            specialization=str(selected.get("specialization") or ""),
        )

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
            booking_select = (
                "COALESCE(a.booking_id, p.booking_id)"
                if self.booking_repository._column_exists(appointment_table, "booking_id")
                else "p.booking_id"
            )
            phone_expr = self.booking_repository._normalized_phone_sql_expr("p.phone")
            today = now_in_runtime_timezone().date().isoformat()
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
                        {booking_select} AS booking_number,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%h:%i %p') AS slot_time
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
                        {booking_select} AS booking_number,
                        DATE_FORMAT(s.slot_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(s.slot_time, '%h:%i %p') AS slot_time
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
        slot_date = now_in_runtime_timezone().date().isoformat()
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

    def _today_schedules(self, *, doctor_id: int, clinic_id: int) -> list[tuple[time, time, int]]:
        if not self.booking_repository:
            return []
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        today = now_in_runtime_timezone().date()
        try:
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
            return self.booking_repository._normalize_schedules(schedules)
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _session_slot_count(*, slot_date: date, start_t: time, end_t: time, duration: int) -> int:
        total_minutes = int((datetime.combine(slot_date, end_t) - datetime.combine(slot_date, start_t)).total_seconds() // 60)
        if total_minutes <= 0 or duration <= 0:
            return 0
        return total_minutes // duration

    @staticmethod
    def _select_overflow_session(
        *,
        slot_date: date,
        schedules: list[tuple[time, time, int]],
        now_dt: datetime,
    ) -> tuple[time, time, int]:
        now_time = now_dt.time().replace(second=0, microsecond=0)
        for start_t, end_t, duration in schedules:
            if start_t <= now_time < end_t:
                return start_t, end_t, duration
        future = [(start_t, end_t, duration) for start_t, end_t, duration in schedules if now_time < start_t]
        if future:
            return future[0]
        return schedules[-1]

    def _select_qr_overflow_session(
        self,
        *,
        schedules: list[tuple[time, time, int]],
        now_dt: datetime,
    ) -> Optional[tuple[time, time, int]]:
        if not schedules:
            return None

        now_local = now_dt.replace(tzinfo=None) if getattr(now_dt, "tzinfo", None) else now_dt
        extension_delta = timedelta(minutes=self.qr_overflow_extension_minutes)
        current_session: Optional[tuple[time, time, int]] = None
        recent_session: Optional[tuple[time, time, int]] = None
        recent_end_dt: Optional[datetime] = None

        for start_t, end_t, duration in schedules:
            start_dt = datetime.combine(now_local.date(), start_t)
            end_dt = datetime.combine(now_local.date(), end_t)
            if start_dt <= now_local < end_dt:
                current_session = (start_t, end_t, duration)
                break
            if end_dt <= now_local <= end_dt + extension_delta:
                if recent_end_dt is None or end_dt > recent_end_dt:
                    recent_session = (start_t, end_t, duration)
                    recent_end_dt = end_dt

        return current_session or recent_session

    def _should_prefer_regular_slot(
        self,
        *,
        slot_date: str,
        slot_time: str,
        now_dt: datetime,
    ) -> bool:
        if not slot_date or not slot_time:
            return False
        now_local = now_dt.replace(tzinfo=None) if getattr(now_dt, "tzinfo", None) else now_dt
        slot_day = datetime.strptime(str(slot_date), "%Y-%m-%d").date()
        parsed_time = None
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"):
            try:
                parsed_time = datetime.strptime(str(slot_time).strip(), fmt).time()
                break
            except ValueError:
                continue
        if parsed_time is None:
            return False
        slot_dt = datetime.combine(slot_day, parsed_time)
        return (
            slot_dt >= now_local
            and slot_dt <= now_local + timedelta(minutes=self.qr_overflow_extension_minutes)
        )

    @staticmethod
    def _regular_slot_starts_for_day(
        *,
        slot_date: date,
        schedules: list[tuple[time, time, int]],
    ) -> set[str]:
        values: set[str] = set()
        for start_t, end_t, duration in schedules:
            cursor = datetime.combine(slot_date, start_t)
            end_dt = datetime.combine(slot_date, end_t)
            step = timedelta(minutes=duration)
            while cursor < end_dt:
                values.add(cursor.strftime("%H:%M"))
                cursor += step
        return values

    @staticmethod
    def _regular_slot_number_for_start(
        *,
        requested_start: time,
        schedules: list[tuple[time, time, int]],
    ) -> Optional[int]:
        requested_dt = datetime.combine(date.today(), requested_start)
        for start_t, end_t, duration in schedules:
            start_dt = datetime.combine(date.today(), start_t)
            end_dt = datetime.combine(date.today(), end_t)
            if requested_dt < start_dt or requested_dt >= end_dt or duration <= 0:
                continue
            diff_minutes = int((requested_dt - start_dt).total_seconds() // 60)
            if diff_minutes % duration != 0:
                return None
            return (diff_minutes // duration) + 1
        return None

    @staticmethod
    def _round_up_to_slot_boundary(value: datetime, duration_minutes: int) -> datetime:
        if duration_minutes <= 0:
            return value.replace(second=0, microsecond=0)
        normalized = value.replace(second=0, microsecond=0)
        minute_mod = normalized.minute % duration_minutes
        if minute_mod == 0:
            return normalized
        return normalized + timedelta(minutes=(duration_minutes - minute_mod))

    def _book_confirmed_overflow(
        self,
        *,
        admin_id: int,
        doctor_id: int,
        clinic_id: int,
        patient_name: str,
        phone: str,
        target_session: Optional[tuple[time, time, int]] = None,
    ) -> tuple[int, int, str, str]:
        conn = self.booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        now_dt = now_in_runtime_timezone()
        today = now_dt.date()
        try:
            conn.start_transaction()
            patient_columns = self.booking_repository._table_columns("patients")
            appointment_table = self.booking_repository._appointment_table()
            appointment_columns = self.booking_repository._table_columns(appointment_table)
            has_channel_col = "channel" in appointment_columns
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

            if target_session is not None:
                session_start, session_end, slot_duration = target_session
            else:
                session_start, session_end, slot_duration = self._select_overflow_session(
                    slot_date=today,
                    schedules=normalized,
                    now_dt=now_dt,
                )
            total_regular_slots = self._session_slot_count(
                slot_date=today,
                start_t=session_start,
                end_t=session_end,
                duration=slot_duration,
            )
            regular_slot_starts = self._regular_slot_starts_for_day(slot_date=today, schedules=normalized)

            phone_expr = self.booking_repository._normalized_phone_sql_expr("phone")
            profile_type_sql = (
                "AND UPPER(COALESCE(profile_type, 'SELF')) = 'SELF'"
                if "profile_type" in patient_columns
                else ""
            )
            cur.execute(
                f"""
                SELECT patient_id
                FROM patients
                WHERE admin_id = %s
                  {profile_type_sql}
                  AND ({phone_expr} = %s OR RIGHT({phone_expr}, 10) = %s)
                ORDER BY patient_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (admin_id, phone, phone[-10:]),
            )
            patient_row = cur.fetchone()
            patient_id = int(patient_row["patient_id"]) if patient_row else None
            if patient_id is None:
                insert_cols = ["full_name", "admin_id", "doctor_id", "phone"]
                insert_vals = [patient_name, admin_id, doctor_id, phone]
                if "profile_type" in patient_columns:
                    insert_cols.append("profile_type")
                    insert_vals.append("SELF")
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

            channel_sql = ""
            params: list[object] = [admin_id, doctor_id, clinic_id, today, session_end]
            if has_channel_col:
                channel_sql = "AND a.channel = %s"
                params.append("qr_scan")
            cur.execute(
                f"""
                SELECT a.start_time
                FROM {appointment_table} a
                WHERE a.admin_id = %s
                  AND a.doctor_id = %s
                  AND a.clinic_id = %s
                  AND a.appointment_date = %s
                  AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED', 'COMPLETED')
                  AND a.start_time > %s
                  {channel_sql}
                ORDER BY a.start_time
                FOR UPDATE
                """,
                tuple(params),
            )
            overflow_count = 0
            for row in cur.fetchall():
                start_value = row.get("start_time")
                if isinstance(start_value, time):
                    hhmm = start_value.strftime("%H:%M")
                else:
                    parsed = self.booking_repository._parse_time_value(start_value)
                    hhmm = parsed.strftime("%H:%M") if parsed else ""
                if hhmm and hhmm not in regular_slot_starts:
                    overflow_count += 1

            overflow_index = overflow_count + 1
            overflow_booking_id = total_regular_slots + overflow_index
            assigned_booking_id = overflow_booking_id
            now_local_dt = now_dt.replace(tzinfo=None) if getattr(now_dt, "tzinfo", None) else now_dt
            overflow_base_dt = now_local_dt
            start_dt = self._round_up_to_slot_boundary(overflow_base_dt, slot_duration)
            end_dt = start_dt + timedelta(minutes=slot_duration)

            while True:
                start_time = start_dt.time().replace(second=0, microsecond=0)
                end_time = end_dt.time().replace(second=0, microsecond=0)
                regular_booking_id = self._regular_slot_number_for_start(
                    requested_start=start_time,
                    schedules=normalized,
                )
                booking_id_for_start = regular_booking_id or overflow_booking_id
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
                    if "booking_id" in appointment_columns:
                        if has_channel_col:
                            cur.execute(
                                f"""
                                INSERT INTO {appointment_table}
                                (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time, booking_id, channel)
                                VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s, %s, %s)
                                """,
                                (patient_id, doctor_id, clinic_id, admin_id, today, start_time, end_time, booking_id_for_start, "qr_scan"),
                            )
                        else:
                            cur.execute(
                                f"""
                                INSERT INTO {appointment_table}
                                (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time, booking_id)
                                VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s, %s)
                                """,
                                (patient_id, doctor_id, clinic_id, admin_id, today, start_time, end_time, booking_id_for_start),
                            )
                    else:
                        if has_channel_col:
                            cur.execute(
                                f"""
                                INSERT INTO {appointment_table}
                                (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time, channel)
                                VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s, %s)
                                """,
                                (patient_id, doctor_id, clinic_id, admin_id, today, start_time, end_time, "qr_scan"),
                            )
                        else:
                            cur.execute(
                                f"""
                                INSERT INTO {appointment_table}
                                (patient_id, doctor_id, clinic_id, admin_id, status, appointment_date, start_time, end_time)
                                VALUES (%s, %s, %s, %s, 'BOOKED', %s, %s, %s)
                                """,
                                (patient_id, doctor_id, clinic_id, admin_id, today, start_time, end_time),
                            )
                    appointment_id = int(cur.lastrowid)
                    assigned_booking_id = booking_id_for_start
                    break

                appt_id = int(appt_row["appointment_id"])
                appt_status = str(appt_row.get("status") or "").upper()
                if appt_status == "CANCELLED":
                    if "booking_id" in appointment_columns:
                        if has_channel_col:
                            cur.execute(
                                f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    status = 'BOOKED',
                                    end_time = %s,
                                    booking_id = %s,
                                    channel = %s
                                WHERE appointment_id = %s
                                """,
                                (patient_id, end_time, booking_id_for_start, "qr_scan", appt_id),
                            )
                        else:
                            cur.execute(
                                f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    status = 'BOOKED',
                                    end_time = %s,
                                    booking_id = %s
                                WHERE appointment_id = %s
                                """,
                                (patient_id, end_time, booking_id_for_start, appt_id),
                            )
                    else:
                        if has_channel_col:
                            cur.execute(
                                f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    status = 'BOOKED',
                                    end_time = %s,
                                    channel = %s
                                WHERE appointment_id = %s
                                """,
                                (patient_id, end_time, "qr_scan", appt_id),
                            )
                        else:
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
                    assigned_booking_id = booking_id_for_start
                    break

                start_dt += timedelta(minutes=slot_duration)
                end_dt = start_dt + timedelta(minutes=slot_duration)

            if "booking_id" in patient_columns:
                cur.execute(
                    """
                    UPDATE patients
                    SET booking_id = %s
                    WHERE patient_id = %s
                    """,
                    (assigned_booking_id, patient_id),
                )
            conn.commit()
            
            # Log SMS confirmation notification (scheduler decides if SMS should send based on .env SMS_ENABLED_CHANNELS)
            try:
                import json
                import logging
                
                logger = logging.getLogger(__name__)
                logger.info(
                    "Logging SMS confirmation notification for QR overflow: appointment_id=%s phone=%s",
                    appointment_id,
                    phone[:4] + "****" if len(phone) > 4 else phone,
                )
                
                meta = json.dumps({"source_channel": "qr_scan"})
                self.booking_repository.log_notification_event(
                    appointment_id=appointment_id,
                    event_type="CONFIRMATION",
                    channel="sms",
                    destination=phone,
                    status="PENDING",
                    admin_id=admin_id,
                    doctor_id=doctor_id,
                    meta_json=meta,
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
            
            return appointment_id, assigned_booking_id, today.isoformat(), start_time.strftime("%I:%M %p").lstrip("0")
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
        today = now_in_runtime_timezone().date()
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
                est_text = est.strftime("%I:%M %p").lstrip("0") if est else ""
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
            estimate_dt = now_in_runtime_timezone() + timedelta(minutes=max(10, next_pos * 8))
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
            return next_pos, estimate_dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def process_checkin(
        self,
        *,
        doctor_id: int,
        clinic_id: int,
        patient_name: str,
        phone: str,
        language: str = "en",
    ) -> QrCheckinResult:
        if not self.booking_repository or not self.scheduling_repository:
            return QrCheckinResult(
                status="error",
                message=get_qr_message(language, "qr_db_not_configured"),
            )

        normalized_phone = self._normalize_phone(phone)
        cleaned_name = " ".join((patient_name or "").strip().split())
        if not cleaned_name:
            return QrCheckinResult(
                status="error",
                message=get_qr_message(language, "qr_enter_patient_name"),
            )
        if len(normalized_phone) < 10 or len(normalized_phone) > 15:
            return QrCheckinResult(
                status="error",
                message=get_qr_message(language, "qr_enter_valid_phone"),
            )

        admin_id = self._resolve_admin_id(doctor_id)
        if not admin_id:
            return QrCheckinResult(
                status="error",
                message=get_qr_message(language, "qr_doctor_admin_missing"),
            )

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
            slot_time = self._format_time_12h(active.get("slot_time") or "")
            suffix = (
                self._msg(language, "active_booking_suffix", slot_date=slot_date, slot_time=slot_time)
                if slot_date or slot_time
                else self._msg(language, "active_booking_suffix_empty")
            )
            return QrCheckinResult(
                status="active_booking",
                message=get_qr_message(
                    language,
                    "qr_active_booking",
                    booking_number=booking_number,
                    slot_date=slot_date,
                    slot_time=slot_time,
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
        now_dt = now_in_runtime_timezone()
        today_schedules = self._today_schedules(doctor_id=doctor_id, clinic_id=clinic_id)
        qr_overflow_session = self._select_qr_overflow_session(
            schedules=today_schedules,
            now_dt=now_dt,
        )
        should_use_regular_slot = self._should_prefer_regular_slot(
            slot_date=slot_date,
            slot_time=slot_time,
            now_dt=now_dt,
        )

        if slot_date and slot_time and (should_use_regular_slot or qr_overflow_session is None):
            display_slot_time = self._format_time_12h(slot_time)
            context = SimpleNamespace(
                patient_name=cleaned_name,
                phone_number=normalized_phone,
                clinic_id=str(clinic_id),
                appointment_date=slot_date,
                appointment_time=slot_time,
                reason="QR Walk-in",
                appointment_mode="walk-in",
                booking_channel="qr_scan",
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
                confirmation_message = get_qr_message(language, "qr_confirmed_token", token_id=number)
                if display_slot_time:
                    confirmation_message += "\n" + get_qr_message(
                        language,
                        "qr_estimated_time",
                        estimated_time=display_slot_time,
                    )
                return QrCheckinResult(
                    status="booked",
                    message=confirmation_message,
                    booking_id=save.appointment_id,
                    appointment_date=slot_date,
                    appointment_time=display_slot_time,
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
                target_session=qr_overflow_session,
            )
        except Exception as exc:
            error_text = str(exc)
            if error_text == "Doctor schedule is not configured for this clinic.":
                error_text = get_qr_message(language, "qr_schedule_unavailable")
            return QrCheckinResult(
                status="error",
                message=get_qr_message(language, "qr_unable_confirm", error=error_text),
            )
        overflow_message = get_qr_message(language, "qr_confirmed_token", token_id=booking_number)
        if overflow_time:
            overflow_message += "\n" + get_qr_message(
                language,
                "qr_estimated_time",
                estimated_time=overflow_time,
            )
        return QrCheckinResult(
            status="booked",
            message=overflow_message,
            booking_id=appointment_id,
            appointment_date=overflow_date,
            appointment_time=overflow_time,
            clinic_name=clinic_name,
            doctor_name=doctor_name,
        )
