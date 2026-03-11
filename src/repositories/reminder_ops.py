from __future__ import annotations

from typing import Any, Optional


def get_appointment_status(repo: Any, appointment_id: int) -> Optional[dict]:
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        appointment_table = repo._appointment_table()
        if repo._use_appointment_mode():
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
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def list_due_doctor_reminders(
    repo: Any,
    lookahead_minutes: int = 180,
    admin_id: Optional[int] = None,
    doctor_reminder_cls: Any = None,
) -> list[Any]:
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        horizon_minutes = max(1, int(lookahead_minutes))
        appointment_table = repo._appointment_table()
        doctor_columns = repo._table_columns("doctors")
        whatsapp_col = "whatsapp_number" if "whatsapp_number" in doctor_columns else None
        telegram_col = None
        for candidate in ("telegram_chat_id", "telegram_user_id", "telegram_id", "chat_id", "user_id"):
            if candidate in doctor_columns:
                telegram_col = candidate
                break
        whatsapp_select = f"NULLIF(d.{whatsapp_col}, '')" if whatsapp_col else "NULL"
        telegram_select = f"NULLIF(d.{telegram_col}, '')" if telegram_col else "NULL"
        ist_now_sql = "CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')"
        if repo._use_appointment_mode():
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
            results: list[Any] = []
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
                    doctor_reminder_cls(
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

        params = [horizon_minutes]
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
        results: list[Any] = []
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
                doctor_reminder_cls(
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
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def get_extra_doctor_contacts(repo: Any, doctor_ids: list[int]) -> dict[int, list[dict]]:
    if not doctor_ids:
        return {}
    try:
        table_cols = repo._table_columns("doctor_whatsapp_numbers")
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

    placeholders = ", ".join(["%s"] * len(doctor_ids))
    conn = repo._connect()
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
        seen: dict[int, set[tuple]] = {}
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
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def is_reminder_sent(repo: Any, *, dedup_key: str) -> bool:
    conn = repo._connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT status FROM doctor_remainder_queue WHERE dedup_key=%s LIMIT 1",
            (dedup_key,),
        )
        row = cur.fetchone()
        return row is not None and str(row[0]).upper() == "SENT"
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def insert_or_get_reminder_queue(
    repo: Any,
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
    conn = repo._connect()
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
                doctor_id,
                schedule_id,
                slot_date,
                schedule_start_time,
                schedule_end_time,
                channel,
                destination,
                lead_minutes,
                dedup_key,
            ),
        )
        queue_id = cur.lastrowid
        conn.commit()
        return int(queue_id)
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def mark_reminder_sent(repo: Any, *, queue_id: int) -> None:
    conn = repo._connect()
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
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def mark_reminder_failed(repo: Any, *, queue_id: int, error: str) -> None:
    conn = repo._connect()
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
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
