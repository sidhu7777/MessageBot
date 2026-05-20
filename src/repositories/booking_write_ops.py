from __future__ import annotations

from datetime import datetime
from typing import Optional


def _normalized_person_name(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def cancel_appointment(
    repo,
    appointment_id: int,
    admin_id: Optional[int] = None,
    cancelled_by: str = "PATIENT",
) -> bool:
    if not appointment_id:
        return False
    conn = repo._connect()
    conn.start_transaction()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            conn.rollback()
            return False
        appointment_table = repo._appointment_table()
        has_cancelled_by = repo._column_exists(appointment_table, "cancelled_by")
        if repo._use_appointment_mode():
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
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()


def reschedule_appointment_same_clinic(
    repo,
    appointment_id: int,
    new_date: str,
    new_time: str,
    new_clinic_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    rescheduled_by: str = "PATIENT",
) -> BookingResult:
    from src.repositories.booking_repository import BookingResult
    if not appointment_id or not new_date or not new_time:
        return BookingResult(False, "Missing required reschedule fields.")
    conn = repo._connect()
    conn.start_transaction()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            conn.rollback()
            return BookingResult(False, "No admin configured.")
        appointment_table = repo._appointment_table()
        has_rescheduled_by = repo._column_exists(appointment_table, "rescheduled_by")
        has_appointment_booking_col = repo._column_exists(appointment_table, "booking_id")

        if repo._use_appointment_mode():
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
            normalized_schedules = repo._normalize_schedules(schedules)
            slot_result = repo._session_slot_index(
                requested_start=start_time,
                schedules=normalized_schedules,
            )
            if slot_result:
                end_time, requested_slot_number = slot_result
                valid = True
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
            if has_appointment_booking_col and requested_slot_number is not None:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET booking_id = %s
                    WHERE appointment_id = %s
                      AND admin_id = %s
                    """,
                    (requested_slot_number, appointment_id, actual_admin_id),
                )
            if repo._column_exists("patients", "booking_id") and requested_slot_number is not None:
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
                "Appointment rescheduled.",
                appointment_id=appointment_id,
                queue_number=requested_slot_number if requested_slot_number is not None else repo.get_daily_queue_number(appointment_id),
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
        if has_appointment_booking_col:
            new_booking_id = repo.get_daily_queue_number(appointment_id)
            if new_booking_id is not None:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET booking_id = %s
                    WHERE appointment_id = %s
                      AND admin_id = %s
                    """,
                    (new_booking_id, appointment_id, actual_admin_id),
                )
        conn.commit()
        return BookingResult(
            True,
            "Appointment rescheduled.",
            appointment_id=appointment_id,
            queue_number=repo.get_daily_queue_number(appointment_id),
        )
    except Exception as exc:
        conn.rollback()
        return BookingResult(False, f"Reschedule transaction failed: {exc}")
    finally:
        cur.close()
        conn.close()


def save_confirmed_appointment(
    repo,
    context,
    admin_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> BookingResult:
    from src.repositories.booking_repository import BookingResult
    if (
        not context.patient_name
        or not context.appointment_date
        or not context.appointment_time
        or not context.clinic_id
    ):
        return BookingResult(False, "Missing required fields; appointment not saved.")

    conn = repo._connect()
    conn.start_transaction()
    cur = conn.cursor(dictionary=True)
    try:
        actual_admin_id = admin_id or repo.default_admin_id()
        if not actual_admin_id:
            conn.rollback()
            return BookingResult(False, "No admin configured.")

        # Build patient upsert dynamically so schema changes do not break booking save.
        patient_columns = repo._table_columns("patients")
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
        appointment_table = repo._appointment_table()
        has_notify_chat_col = repo._column_exists(appointment_table, "notify_telegram_chat_id")
        has_appointment_booking_col = repo._column_exists(appointment_table, "booking_id")
        has_booked_for_col = repo._column_exists(appointment_table, "booked_for")
        has_channel_col = repo._column_exists(appointment_table, "channel")
        raw_channel_value = str(getattr(context, "booking_channel", "") or "").strip().lower()
        if not raw_channel_value:
            appointment_mode = str(getattr(context, "appointment_mode", "") or "").strip().lower()
            if appointment_mode == "whatsapp-web":
                raw_channel_value = "whatsapp_web"
            elif appointment_mode == "walk-in":
                raw_channel_value = "qr_scan"
            elif repo._normalize_chat_user_id(str(getattr(context, "chat_user_id", "") or "").strip()):
                raw_channel_value = "telegram"
        valid_channel_values = {"qr_scan", "whatsapp_web", "telegram", "app", "web"}
        channel_value = raw_channel_value if raw_channel_value in valid_channel_values else ""

        patient_values: dict[str, object] = {}
        booking_for_self = getattr(context, "booking_for_self", None)
        profile_type_value: Optional[str] = None
        if booking_for_self is True:
            profile_type_value = "SELF"
        elif booking_for_self is False:
            profile_type_value = "OTHER"
        if "full_name" in patient_columns:
            patient_values["full_name"] = context.patient_name
        if "admin_id" in patient_columns:
            patient_values["admin_id"] = actual_admin_id
        if "phone" in patient_columns:
            patient_values["phone"] = context.phone_number
        if "profile_type" in patient_columns and profile_type_value is not None:
            patient_values["profile_type"] = profile_type_value
        chat_user_id_value = repo._normalize_chat_user_id(
            str(getattr(context, "chat_user_id", "") or "")
        )
        chat_column: Optional[str] = None
        for candidate in ("telegram_chat_id", "telegram_user_id", "user_id"):
            if candidate in patient_columns:
                chat_column = candidate
                break
        if chat_column and chat_user_id_value and booking_for_self is True:
            patient_values[chat_column] = chat_user_id_value
        if "age" in patient_columns:
            patient_values["age"] = getattr(context, "age", None)
        if "gender" in patient_columns:
            patient_values["gender"] = getattr(context, "gender", None)
        if "patient_type" in patient_columns:
            patient_values["patient_type"] = getattr(context, "patient_type", None)
        if "reason" in patient_columns:
            patient_values["reason"] = reason_value
        if mode_column:
            patient_values[mode_column] = mode_value

        # Prefer chat-id match for Telegram self-booking to avoid duplicate-key conflicts
        # on patients.telegram_chat_id when name/phone differ from historical data.
        patient_id: Optional[int] = None
        if chat_column and chat_user_id_value and booking_for_self is True:
            cur.execute(
                f"""
                SELECT patient_id
                FROM patients
                WHERE admin_id = %s
                  AND TRIM(COALESCE({chat_column}, '')) = %s
                ORDER BY patient_id DESC
                LIMIT 1
                """,
                (actual_admin_id, chat_user_id_value),
            )
            by_chat = cur.fetchone()
            if by_chat:
                patient_id = int(by_chat["patient_id"])

        normalized_phone = repo._normalize_phone(str(context.phone_number or ""))

        # For self bookings, keep one self profile per phone from now on.
        # Existing historical duplicates are left untouched; future bookings reuse
        # the newest matching SELF row instead of creating another one.
        if (
            patient_id is None
            and booking_for_self is True
            and normalized_phone
            and "profile_type" in patient_columns
        ):
            phone_expr = repo._normalized_phone_sql_expr("phone")
            cur.execute(
                f"""
                SELECT patient_id
                FROM patients
                WHERE admin_id = %s
                  AND UPPER(COALESCE(profile_type, '')) = 'SELF'
                  AND ({phone_expr} = %s OR RIGHT({phone_expr}, 10) = %s)
                ORDER BY patient_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (actual_admin_id, normalized_phone, normalized_phone[-10:]),
            )
            self_row = cur.fetchone()
            if self_row:
                patient_id = int(self_row["patient_id"])

        # Fallback lookup by name/admin, and phone when present.
        if patient_id is None:
            lookup_sql = (
                "SELECT patient_id "
                "FROM patients "
                "WHERE full_name = %s AND admin_id = %s"
            )
            lookup_params: list[object] = [context.patient_name, actual_admin_id]
            if normalized_phone:
                lookup_sql += " AND REPLACE(REPLACE(REPLACE(COALESCE(phone,''), ' ', ''), '-', ''), '+', '') LIKE %s"
                lookup_params.append(f"%{normalized_phone[-10:]}")
            lookup_sql += " ORDER BY patient_id LIMIT 1"
            cur.execute(lookup_sql, tuple(lookup_params))
            patient_row = cur.fetchone()
            if patient_row:
                patient_id = int(patient_row["patient_id"])

        update_columns = [
            col
            for col in ("full_name", "profile_type", "phone", "age", "gender", "patient_type", "reason", mode_column, chat_column)
            if col and col in patient_values
        ]

        def _update_patient_from_values(target_patient_id: int) -> None:
            if not update_columns:
                return
            assignments = ", ".join(f"{col} = %s" for col in update_columns)
            update_params = [patient_values[col] for col in update_columns]
            update_params.append(target_patient_id)
            cur.execute(
                f"""
                UPDATE patients
                SET {assignments}
                WHERE patient_id = %s
                """,
                tuple(update_params),
            )

        if patient_id is not None:
            _update_patient_from_values(patient_id)
        else:
            insert_columns = [
                col
                for col in ("full_name", "admin_id", "profile_type", "phone", "age", "gender", "patient_type", "reason", mode_column, chat_column)
                if col and col in patient_values
            ]
            if "full_name" not in insert_columns or "admin_id" not in insert_columns:
                conn.rollback()
                return BookingResult(False, "Patients schema missing required columns.")
            placeholders = ", ".join(["%s"] * len(insert_columns))
            col_sql = ", ".join(insert_columns)
            params = [patient_values[col] for col in insert_columns]
            try:
                cur.execute(
                    f"""
                    INSERT INTO patients
                    ({col_sql})
                    VALUES ({placeholders})
                    """,
                    tuple(params),
                )
                patient_id = int(cur.lastrowid)
            except Exception as _dup_exc:
                # If it's NOT a duplicate-key error, re-raise so the outer handler catches it.
                if "1062" not in str(_dup_exc) and "Duplicate entry" not in str(_dup_exc):
                    raise
                # Duplicate key on telegram_chat_id (or phone) — recover by fetching existing row.
                _recovered = False
                admin_col_exists = "admin_id" in patient_columns
                select_cols = "patient_id, admin_id" if admin_col_exists else "patient_id"
                if chat_column and chat_user_id_value:
                    # Prefer same-admin chat-id match.
                    cur.execute(
                        f"""
                        SELECT {select_cols} FROM patients
                        WHERE admin_id = %s
                          AND TRIM(COALESCE({chat_column}, '')) = %s
                        ORDER BY patient_id DESC LIMIT 1
                        """,
                        (actual_admin_id, chat_user_id_value),
                    )
                    _dup_row = cur.fetchone()
                    if _dup_row:
                        patient_id = int(_dup_row["patient_id"])
                        _recovered = True

                if not _recovered and chat_column and chat_user_id_value:
                    # Detect conflicting cross-admin ownership instead of silently linking.
                    cur.execute(
                        f"""
                        SELECT {select_cols} FROM patients
                        WHERE TRIM(COALESCE({chat_column}, '')) = %s
                        ORDER BY patient_id DESC LIMIT 1
                        """,
                        (chat_user_id_value,),
                    )
                    _dup_row = cur.fetchone()
                    if _dup_row:
                        if admin_col_exists:
                            row_admin_id = _dup_row.get("admin_id")
                            if row_admin_id is not None and int(row_admin_id) != int(actual_admin_id):
                                conn.rollback()
                                return BookingResult(
                                    False,
                                    "Telegram chat id is linked to a different admin profile.",
                                )
                        patient_id = int(_dup_row["patient_id"])
                        _recovered = True
                if not _recovered:
                    # Try fallback by phone
                    _norm = repo._normalize_phone(str(context.phone_number or ""))
                    if _norm:
                        cur.execute(
                            """
                            SELECT patient_id FROM patients
                            WHERE admin_id = %s
                              AND REPLACE(REPLACE(REPLACE(COALESCE(phone,''),' ',''),'-',''),'+','') LIKE %s
                            ORDER BY patient_id DESC LIMIT 1
                            """,
                            (actual_admin_id, f"%{_norm[-10:]}"),
                        )
                        _dup_row = cur.fetchone()
                        if _dup_row:
                            patient_id = int(_dup_row["patient_id"])
                            _recovered = True
                if not _recovered:
                    raise
                _update_patient_from_values(int(patient_id))

        booked_for_value: Optional[str] = None
        if booking_for_self is True:
            booked_for_value = "SELF"
        elif booking_for_self is False:
            booked_for_value = "OTHER"

        def _has_other_active_booking() -> bool:
            if patient_id is None or booked_for_value != "OTHER" or not has_booked_for_col:
                return False
            active_statuses = ("BOOKED", "PENDING", "CONFIRMED")
            if repo._use_appointment_mode():
                cur.execute(
                    f"""
                    SELECT appointment_id
                    FROM {appointment_table}
                    WHERE patient_id = %s
                      AND admin_id = %s
                      AND booked_for = 'OTHER'
                      AND status IN (%s, %s, %s)
                    ORDER BY appointment_id DESC
                    LIMIT 1
                    """,
                    (patient_id, actual_admin_id, *active_statuses),
                )
                return cur.fetchone() is not None
            cur.execute(
                f"""
                SELECT a.appointment_id
                FROM {appointment_table} a
                WHERE a.patient_id = %s
                  AND a.admin_id = %s
                  AND a.booked_for = 'OTHER'
                  AND a.status IN (%s, %s, %s)
                ORDER BY a.appointment_id DESC
                LIMIT 1
                """,
                (patient_id, actual_admin_id, *active_statuses),
            )
            return cur.fetchone() is not None

        if repo._use_appointment_mode():
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

            normalized_name = _normalized_person_name(context.patient_name)
            if normalized_phone and normalized_name:
                phone_expr = repo._normalized_phone_sql_expr("p.phone")
                cur.execute(
                    f"""
                    SELECT
                        a.appointment_id,
                        DATE_FORMAT(a.appointment_date, '%Y-%m-%d') AS slot_date,
                        TIME_FORMAT(a.start_time, '%H:%i') AS slot_time
                    FROM {appointment_table} a
                    JOIN patients p ON p.patient_id = a.patient_id
                    WHERE a.admin_id = %s
                      AND a.doctor_id = %s
                      AND a.appointment_date = %s
                      AND a.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
                      AND LOWER(TRIM(COALESCE(p.full_name, ''))) = %s
                      AND ({phone_expr} = %s OR RIGHT({phone_expr}, 10) = %s)
                    ORDER BY a.start_time DESC, a.appointment_id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (
                        actual_admin_id,
                        resolved_doctor_id,
                        context.appointment_date,
                        normalized_name,
                        normalized_phone,
                        normalized_phone[-10:],
                    ),
                )
                same_day_identity = cur.fetchone()
                if same_day_identity:
                    conn.rollback()
                    return BookingResult(
                        False,
                        (
                            "Patient already has an active appointment for this date. "
                            "Please cancel or reschedule the existing appointment first."
                        ),
                        appointment_id=int(same_day_identity["appointment_id"]),
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
            normalized_schedules = repo._normalize_schedules(schedules)
            slot_result = repo._session_slot_index(
                requested_start=requested_start,
                schedules=normalized_schedules,
            )
            if slot_result:
                requested_end, requested_slot_number = slot_result
                matched = True
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
                existing_updates: list[str] = []
                existing_params: list[object] = []
                if has_appointment_booking_col and requested_slot_number is not None:
                    existing_updates.append("booking_id = %s")
                    existing_params.append(requested_slot_number)
                if has_booked_for_col and booked_for_value is not None:
                    existing_updates.append("booked_for = %s")
                    existing_params.append(booked_for_value)
                if has_channel_col and channel_value:
                    existing_updates.append("channel = %s")
                    existing_params.append(channel_value)
                if existing_updates:
                    existing_params.append(int(existing["appointment_id"]))
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET {", ".join(existing_updates)}
                        WHERE appointment_id = %s
                        """,
                        tuple(existing_params),
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
                appt_id = int(existing["appointment_id"])
                return BookingResult(
                    True,
                    "Appointment already exists.",
                    appointment_id=appt_id,
                    queue_number=requested_slot_number if requested_slot_number is not None else repo.get_daily_queue_number(appt_id),
                )

            if _has_other_active_booking():
                conn.rollback()
                return BookingResult(
                    False,
                    "Another active appointment already exists for this other person. Please cancel or complete the existing booking first.",
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
                    if has_notify_chat_col:
                        if has_appointment_booking_col:
                            update_sql = f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    notify_telegram_chat_id = %s,
                                    booking_id = %s,
                                    status = 'BOOKED'
                            """
                            update_params: list[object] = [
                                patient_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                requested_end,
                                chat_user_id_value or None,
                                requested_slot_number,
                            ]
                            if has_booked_for_col and booked_for_value is not None:
                                update_sql += ",\n                                    booked_for = %s"
                                update_params.append(booked_for_value)
                            if has_channel_col and channel_value:
                                update_sql += ",\n                                    channel = %s"
                                update_params.append(channel_value)
                            update_sql += "\n                                WHERE appointment_id = %s"
                            update_params.append(slot_appointment_id)
                            cur.execute(update_sql, tuple(update_params))
                        else:
                            update_sql = f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    notify_telegram_chat_id = %s,
                                    status = 'BOOKED'
                            """
                            update_params = [
                                patient_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                requested_end,
                                chat_user_id_value or None,
                            ]
                            if has_booked_for_col and booked_for_value is not None:
                                update_sql += ",\n                                    booked_for = %s"
                                update_params.append(booked_for_value)
                            if has_channel_col and channel_value:
                                update_sql += ",\n                                    channel = %s"
                                update_params.append(channel_value)
                            update_sql += "\n                                WHERE appointment_id = %s"
                            update_params.append(slot_appointment_id)
                            cur.execute(update_sql, tuple(update_params))
                    else:
                        if has_appointment_booking_col:
                            update_sql = f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    booking_id = %s,
                                    status = 'BOOKED'
                            """
                            update_params = [
                                patient_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                requested_end,
                                requested_slot_number,
                            ]
                            if has_booked_for_col and booked_for_value is not None:
                                update_sql += ",\n                                    booked_for = %s"
                                update_params.append(booked_for_value)
                            if has_channel_col and channel_value:
                                update_sql += ",\n                                    channel = %s"
                                update_params.append(channel_value)
                            update_sql += "\n                                WHERE appointment_id = %s"
                            update_params.append(slot_appointment_id)
                            cur.execute(update_sql, tuple(update_params))
                        else:
                            update_sql = f"""
                                UPDATE {appointment_table}
                                SET patient_id = %s,
                                    clinic_id = %s,
                                    admin_id = %s,
                                    end_time = %s,
                                    status = 'BOOKED'
                            """
                            update_params = [
                                patient_id,
                                int(context.clinic_id),
                                actual_admin_id,
                                requested_end,
                            ]
                            if has_booked_for_col and booked_for_value is not None:
                                update_sql += ",\n                                    booked_for = %s"
                                update_params.append(booked_for_value)
                            if has_channel_col and channel_value:
                                update_sql += ",\n                                    channel = %s"
                                update_params.append(channel_value)
                            update_sql += "\n                                WHERE appointment_id = %s"
                            update_params.append(slot_appointment_id)
                            cur.execute(update_sql, tuple(update_params))
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
                        queue_number=requested_slot_number if requested_slot_number is not None else repo.get_daily_queue_number(slot_appointment_id),
                    )
                conn.rollback()
                return BookingResult(False, "Selected slot is not available.")

            try:
                if has_notify_chat_col:
                    if has_appointment_booking_col:
                        insert_cols = [
                            "patient_id",
                            "doctor_id",
                            "clinic_id",
                            "admin_id",
                            "status",
                            "appointment_date",
                            "start_time",
                            "end_time",
                            "notify_telegram_chat_id",
                            "booking_id",
                        ]
                        insert_vals: list[object] = [
                            patient_id,
                            resolved_doctor_id,
                            int(context.clinic_id),
                            actual_admin_id,
                            "BOOKED",
                            context.appointment_date,
                            requested_start,
                            requested_end,
                            chat_user_id_value or None,
                            requested_slot_number,
                        ]
                        if has_booked_for_col and booked_for_value is not None:
                            insert_cols.append("booked_for")
                            insert_vals.append(booked_for_value)
                        if has_channel_col and channel_value:
                            insert_cols.append("channel")
                            insert_vals.append(channel_value)
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            ({", ".join(insert_cols)})
                            VALUES ({", ".join(["%s"] * len(insert_cols))})
                            """,
                            tuple(insert_vals),
                        )
                    else:
                        insert_cols = [
                            "patient_id",
                            "doctor_id",
                            "clinic_id",
                            "admin_id",
                            "status",
                            "appointment_date",
                            "start_time",
                            "end_time",
                            "notify_telegram_chat_id",
                        ]
                        insert_vals = [
                            patient_id,
                            resolved_doctor_id,
                            int(context.clinic_id),
                            actual_admin_id,
                            "BOOKED",
                            context.appointment_date,
                            requested_start,
                            requested_end,
                            chat_user_id_value or None,
                        ]
                        if has_booked_for_col and booked_for_value is not None:
                            insert_cols.append("booked_for")
                            insert_vals.append(booked_for_value)
                        if has_channel_col and channel_value:
                            insert_cols.append("channel")
                            insert_vals.append(channel_value)
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            ({", ".join(insert_cols)})
                            VALUES ({", ".join(["%s"] * len(insert_cols))})
                            """,
                            tuple(insert_vals),
                        )
                else:
                    if has_appointment_booking_col:
                        insert_cols = [
                            "patient_id",
                            "doctor_id",
                            "clinic_id",
                            "admin_id",
                            "status",
                            "appointment_date",
                            "start_time",
                            "end_time",
                            "booking_id",
                        ]
                        insert_vals = [
                            patient_id,
                            resolved_doctor_id,
                            int(context.clinic_id),
                            actual_admin_id,
                            "BOOKED",
                            context.appointment_date,
                            requested_start,
                            requested_end,
                            requested_slot_number,
                        ]
                        if has_booked_for_col and booked_for_value is not None:
                            insert_cols.append("booked_for")
                            insert_vals.append(booked_for_value)
                        if has_channel_col and channel_value:
                            insert_cols.append("channel")
                            insert_vals.append(channel_value)
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            ({", ".join(insert_cols)})
                            VALUES ({", ".join(["%s"] * len(insert_cols))})
                            """,
                            tuple(insert_vals),
                        )
                    else:
                        insert_cols = [
                            "patient_id",
                            "doctor_id",
                            "clinic_id",
                            "admin_id",
                            "status",
                            "appointment_date",
                            "start_time",
                            "end_time",
                        ]
                        insert_vals = [
                            patient_id,
                            resolved_doctor_id,
                            int(context.clinic_id),
                            actual_admin_id,
                            "BOOKED",
                            context.appointment_date,
                            requested_start,
                            requested_end,
                        ]
                        if has_booked_for_col and booked_for_value is not None:
                            insert_cols.append("booked_for")
                            insert_vals.append(booked_for_value)
                        if has_channel_col and channel_value:
                            insert_cols.append("channel")
                            insert_vals.append(channel_value)
                        cur.execute(
                            f"""
                            INSERT INTO {appointment_table}
                            ({", ".join(insert_cols)})
                            VALUES ({", ".join(["%s"] * len(insert_cols))})
                            """,
                            tuple(insert_vals),
                        )
            except Exception as _insert_exc:
                if "1062" not in str(_insert_exc) and "Duplicate entry" not in str(_insert_exc):
                    raise
                conn.rollback()
                return BookingResult(False, "Selected slot is not available.")
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
                queue_number=requested_slot_number if requested_slot_number is not None else repo.get_daily_queue_number(appointment_id),
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
            if has_notify_chat_col and chat_user_id_value:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET notify_telegram_chat_id = COALESCE(NULLIF(TRIM(notify_telegram_chat_id), ''), %s)
                    WHERE appointment_id = %s
                    """,
                    (chat_user_id_value, int(existing["appointment_id"])),
                )
            if has_appointment_booking_col:
                existing_booking_id = repo.get_daily_queue_number(int(existing["appointment_id"]))
                if existing_booking_id is not None:
                    cur.execute(
                        f"""
                        UPDATE {appointment_table}
                        SET booking_id = COALESCE(booking_id, %s)
                        WHERE appointment_id = %s
                        """,
                        (existing_booking_id, int(existing["appointment_id"])),
                    )
            if has_booked_for_col and booked_for_value is not None:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET booked_for = %s
                    WHERE appointment_id = %s
                    """,
                    (booked_for_value, int(existing["appointment_id"])),
                )
            if has_channel_col and channel_value:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET channel = %s
                    WHERE appointment_id = %s
                    """,
                    (channel_value, int(existing["appointment_id"])),
                )
            conn.commit()
            appt_id = int(existing["appointment_id"])
            return BookingResult(
                True,
                "Appointment already exists.",
                appointment_id=appt_id,
                queue_number=repo.get_daily_queue_number(appt_id),
            )

        if _has_other_active_booking():
            conn.rollback()
            return BookingResult(
                False,
                "Another active appointment already exists for this other person. Please cancel or complete the existing booking first.",
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

        if has_notify_chat_col:
            if has_appointment_booking_col:
                insert_cols = [
                    "patient_id",
                    "slot_id",
                    "doctor_id",
                    "clinic_id",
                    "admin_id",
                    "status",
                    "notify_telegram_chat_id",
                    "booking_id",
                ]
                insert_vals = [
                    patient_id,
                    slot_id,
                    doctor_id,
                    clinic_id,
                    actual_admin_id,
                    "BOOKED",
                    chat_user_id_value or None,
                    None,
                ]
                if has_booked_for_col and booked_for_value is not None:
                    insert_cols.append("booked_for")
                    insert_vals.append(booked_for_value)
                if has_channel_col and channel_value:
                    insert_cols.append("channel")
                    insert_vals.append(channel_value)
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    ({", ".join(insert_cols)})
                    VALUES ({", ".join(["%s"] * len(insert_cols))})
                    """,
                    tuple(insert_vals),
                )
            else:
                insert_cols = [
                    "patient_id",
                    "slot_id",
                    "doctor_id",
                    "clinic_id",
                    "admin_id",
                    "status",
                    "notify_telegram_chat_id",
                ]
                insert_vals = [
                    patient_id,
                    slot_id,
                    doctor_id,
                    clinic_id,
                    actual_admin_id,
                    "BOOKED",
                    chat_user_id_value or None,
                ]
                if has_booked_for_col and booked_for_value is not None:
                    insert_cols.append("booked_for")
                    insert_vals.append(booked_for_value)
                if has_channel_col and channel_value:
                    insert_cols.append("channel")
                    insert_vals.append(channel_value)
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    ({", ".join(insert_cols)})
                    VALUES ({", ".join(["%s"] * len(insert_cols))})
                    """,
                    tuple(insert_vals),
                )
        else:
            if has_appointment_booking_col:
                insert_cols = [
                    "patient_id",
                    "slot_id",
                    "doctor_id",
                    "clinic_id",
                    "admin_id",
                    "status",
                    "booking_id",
                ]
                insert_vals = [
                    patient_id,
                    slot_id,
                    doctor_id,
                    clinic_id,
                    actual_admin_id,
                    "BOOKED",
                    None,
                ]
                if has_booked_for_col and booked_for_value is not None:
                    insert_cols.append("booked_for")
                    insert_vals.append(booked_for_value)
                if has_channel_col and channel_value:
                    insert_cols.append("channel")
                    insert_vals.append(channel_value)
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    ({", ".join(insert_cols)})
                    VALUES ({", ".join(["%s"] * len(insert_cols))})
                    """,
                    tuple(insert_vals),
                )
            else:
                insert_cols = [
                    "patient_id",
                    "slot_id",
                    "doctor_id",
                    "clinic_id",
                    "admin_id",
                    "status",
                ]
                insert_vals = [
                    patient_id,
                    slot_id,
                    doctor_id,
                    clinic_id,
                    actual_admin_id,
                    "BOOKED",
                ]
                if has_booked_for_col and booked_for_value is not None:
                    insert_cols.append("booked_for")
                    insert_vals.append(booked_for_value)
                if has_channel_col and channel_value:
                    insert_cols.append("channel")
                    insert_vals.append(channel_value)
                cur.execute(
                    f"""
                    INSERT INTO {appointment_table}
                    ({", ".join(insert_cols)})
                    VALUES ({", ".join(["%s"] * len(insert_cols))})
                    """,
                    tuple(insert_vals),
                )
        appointment_id = int(cur.lastrowid)
        if has_appointment_booking_col:
            booking_id = repo.get_daily_queue_number(appointment_id)
            if booking_id is not None:
                cur.execute(
                    f"""
                    UPDATE {appointment_table}
                    SET booking_id = %s
                    WHERE appointment_id = %s
                    """,
                    (booking_id, appointment_id),
                )
        conn.commit()
        return BookingResult(
            True,
            "Appointment persisted.",
            appointment_id=appointment_id,
            queue_number=repo.get_daily_queue_number(appointment_id),
        )
    except Exception as exc:
        conn.rollback()
        return BookingResult(False, f"Booking transaction failed: {exc}")
    finally:
        cur.close()
        conn.close()
