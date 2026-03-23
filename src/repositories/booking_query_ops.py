from __future__ import annotations

from typing import Optional


def get_doctor_display_name(repo, doctor_id: Optional[int], admin_id: Optional[int] = None) -> Optional[str]:
    if doctor_id is None:
        return None
    conn = repo._connect()
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
    repo,
    phone_number: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> Optional[str]:
    target = repo._normalize_phone(phone_number)
    if not target:
        return None
    actual_admin_id = admin_id or repo.default_admin_id()
    if not actual_admin_id:
        return None
    cache_key = repo._patient_phone_cache_key(
        admin_id=actual_admin_id,
        doctor_id=doctor_id,
        phone_number=target,
    )
    cached = repo._load_cached_patient_identity(key=cache_key)
    if isinstance(cached, dict):
        cached_name = str(cached.get("full_name") or "").strip()
        if cached_name:
            return cached_name
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
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
                patient_name = str(row.get("full_name") or "").strip() or None
                if patient_name:
                    repo._save_cached_patient_identity(
                        key=cache_key,
                        full_name=patient_name,
                        phone_number=target,
                    )
                return patient_name

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
        patient_name = str(row.get("full_name") or "").strip() or None
        if patient_name:
            repo._save_cached_patient_identity(
                key=cache_key,
                full_name=patient_name,
                phone_number=target,
            )
        return patient_name
    finally:
        cur.close()
        conn.close()


def find_patient_name_by_chat_user_id(
    repo,
    chat_user_id: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> Optional[str]:
    """Look up patient full_name using telegram_chat_id stored in the patients table."""
    target = repo._normalize_chat_user_id(chat_user_id)
    if not target:
        return None
    actual_admin_id = admin_id or repo.default_admin_id()
    if not actual_admin_id:
        return None
    cache_key = repo._patient_chat_cache_key(
        admin_id=actual_admin_id,
        doctor_id=doctor_id,
        chat_user_id=target,
    )
    cached = repo._load_cached_patient_identity(key=cache_key)
    if isinstance(cached, dict):
        cached_name = str(cached.get("full_name") or "").strip()
        if cached_name:
            return cached_name
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        chat_col = repo._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
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
            patient_name = str(row.get("full_name") or "").strip() or None
            if patient_name:
                repo._save_cached_patient_identity(key=cache_key, full_name=patient_name)
            return patient_name
        return None
    finally:
        cur.close()
        conn.close()


def find_patient_phone_by_chat_user_id(
    repo,
    chat_user_id: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> Optional[str]:
    """Return the phone number stored in patients table for a known Telegram patient."""
    target = repo._normalize_chat_user_id(chat_user_id)
    if not target:
        return None
    actual_admin_id = admin_id or repo.default_admin_id()
    if not actual_admin_id:
        return None
    cache_key = repo._patient_chat_cache_key(
        admin_id=actual_admin_id,
        doctor_id=doctor_id,
        chat_user_id=target,
    )
    cached = repo._load_cached_patient_identity(key=cache_key)
    if isinstance(cached, dict):
        cached_phone = repo._normalize_phone(str(cached.get("phone_number") or "").strip())
        if cached_phone:
            return cached_phone
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        chat_col = repo._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
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
            phone_number = repo._normalize_phone(str(row.get("phone") or "").strip()) or None
            repo._save_cached_patient_identity(
                key=cache_key,
                full_name="",
                phone_number=phone_number,
            )
            return phone_number
        return None
    finally:
        cur.close()
        conn.close()


def find_active_appointment_by_patient_name(
    repo,
    patient_name: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> Optional[dict]:
    if not patient_name:
        return None
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            return None
        appointment_table = repo._appointment_table()
        if repo._use_appointment_mode():
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
    repo,
    phone_number: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> Optional[dict]:
    target = repo._normalize_phone(phone_number)
    if not target:
        return None

    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            return None
        appointment_table = repo._appointment_table()
        booking_select = (
            "COALESCE(a.booking_id, p.booking_id)"
            if repo._column_exists(appointment_table, "booking_id")
            else "p.booking_id"
        )
        phone_expr = repo._normalized_phone_sql_expr("p.phone")
        phone_filter_sql = f"AND ({phone_expr} = %s"
        params_phone: list[object] = [target]
        if len(target) >= 10:
            phone_filter_sql += f" OR RIGHT({phone_expr}, 10) = %s"
            params_phone.append(target[-10:])
        phone_filter_sql += ")"
        if repo._use_appointment_mode():
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
                    {booking_select} AS booking_number,
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
                if not repo._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
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
            if not repo._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                continue
            return row
        return None
    finally:
        cur.close()
        conn.close()


def list_active_appointments_by_phone_number(
    repo,
    phone_number: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    limit: int = 10,
) -> list[dict]:
    target = repo._normalize_phone(phone_number)
    if not target:
        return []

    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            return []
        appointment_table = repo._appointment_table()
        booking_select = (
            "COALESCE(a.booking_id, p.booking_id)"
            if repo._column_exists(appointment_table, "booking_id")
            else "p.booking_id"
        )
        phone_expr = repo._normalized_phone_sql_expr("p.phone")
        phone_filter_sql = f"AND ({phone_expr} = %s"
        params_phone: list[object] = [target]
        if len(target) >= 10:
            phone_filter_sql += f" OR RIGHT({phone_expr}, 10) = %s"
            params_phone.append(target[-10:])
        phone_filter_sql += ")"
        if repo._use_appointment_mode():
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
                    {booking_select} AS booking_number,
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
                    {booking_select} AS booking_number,
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
            if not repo._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                continue
            matched.append(row)
            if len(matched) >= max(1, limit):
                break
        return matched
    finally:
        cur.close()
        conn.close()


def list_active_appointments_by_chat_user_id(
    repo,
    chat_user_id: str,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    limit: int = 10,
) -> list[dict]:
    target = repo._normalize_chat_user_id(chat_user_id)
    if not target:
        return []

    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            return []
        chat_col = repo._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
        if not chat_col:
            return []

        appointment_table = repo._appointment_table()
        booking_select = (
            "COALESCE(a.booking_id, p.booking_id)"
            if repo._column_exists(appointment_table, "booking_id")
            else "p.booking_id"
        )
        params: list[object] = [actual_admin_id, target]
        doctor_sql = ""
        if doctor_id is not None:
            doctor_sql = "AND a.doctor_id = %s"
            params.append(doctor_id)

        if repo._use_appointment_mode():
            cur.execute(
                f"""
                SELECT
                    a.appointment_id,
                    a.clinic_id,
                    a.doctor_id,
                    {booking_select} AS booking_number,
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
                    {booking_select} AS booking_number,
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
            if not repo._is_actionable_booking_row(row.get("slot_date"), row.get("slot_time")):
                continue
            matched.append(row)
            if len(matched) >= max(1, limit):
                break
        return matched
    finally:
        cur.close()
        conn.close()
