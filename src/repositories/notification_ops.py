from __future__ import annotations

from typing import Any, Optional


def log_notification_event(
    repo: Any,
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
    conn = repo._connect()
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


def list_pending_notification_events(
    repo: Any,
    *,
    limit: int = 200,
    admin_id: Optional[int] = None,
    notification_event_cls: Any,
) -> list[Any]:
    conn = repo._connect()
    cur = conn.cursor(dictionary=True)
    try:
        safe_limit = max(1, min(1000, int(limit)))
        appointment_table = repo._appointment_table()
        chat_col = repo._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
        chat_select = f"COALESCE(p.{chat_col}, '')" if chat_col else "''"

        params: list[object] = []
        admin_sql = ""
        if admin_id is not None:
            admin_sql = "AND l.admin_id = %s"
            params.append(admin_id)

        if repo._use_appointment_mode():
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
                  AND l.event_type IN ('CANCELLED', 'RESCHEDULED', 'DOCTOR_DELAYED')
                  {admin_sql}
                ORDER BY l.notification_id
                LIMIT {safe_limit}
                """,
                tuple(params),
            )

        rows = cur.fetchall()
        events: list[Any] = []
        for row in rows:
            events.append(
                notification_event_cls(
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
    repo: Any,
    *,
    notification_id: int,
    status: str,
    error_text: str = "",
    provider_message_sid: str = "",
) -> None:
    if not notification_id:
        return
    normalized = (status or "").strip().upper() or "FAILED"
    conn = repo._connect()
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
    repo: Any,
    *,
    limit: int = 100,
    worker_id: str,
    admin_id: Optional[int] = None,
    notification_event_cls: Any,
) -> list[Any]:
    conn = repo._connect()
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

        appointment_table = repo._appointment_table()
        chat_col = repo._first_existing_column("patients", ("telegram_chat_id", "telegram_user_id", "user_id"))
        chat_select = f"COALESCE(p.{chat_col}, '')" if chat_col else "''"

        if repo._use_appointment_mode():
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
        events: list[Any] = []
        for row in rows:
            notification_id = row.get("notification_id")
            appointment_id = row.get("appointment_id")
            if notification_id is None or appointment_id is None:
                continue
            events.append(
                notification_event_cls(
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
    repo: Any,
    *,
    notification_id: int,
    error_text: str,
    backoff_seconds: int,
    max_attempts: int,
) -> None:
    if not notification_id:
        return
    conn = repo._connect()
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
    repo: Any,
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
    conn = repo._connect()
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


def notification_queue_stats(repo: Any) -> dict:
    conn = repo._connect()
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
