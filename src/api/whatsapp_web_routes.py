from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from src.messages.templates import get_message
from src.whatsapp_web.page_renderer import render_whatsapp_web_page_html


_SUPPORTED_LANGS = {"en", "hi", "hinglish"}


def _normalize_lang(value: str | None, *, allow_hinglish: bool = True) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw == "hinglish":
        return "hinglish" if allow_hinglish else None
    if raw.startswith("hi"):
        return "hi"
    if raw.startswith("en"):
        return "en"
    if raw in _SUPPORTED_LANGS and (allow_hinglish or raw != "hinglish"):
        return raw
    return None


def _lang_from_accept_header(header_value: str | None) -> str | None:
    raw = str(header_value or "")
    if not raw:
        return None
    for item in raw.split(","):
        token = item.split(";")[0].strip().lower()
        normalized = _normalize_lang(token, allow_hinglish=False)
        if normalized in {"en", "hi"}:
            return normalized
    return None


def _resolve_effective_language(request: Request, payload: dict[str, object] | None = None) -> tuple[str, bool]:
    payload = payload or {}
    query_lang = _normalize_lang(request.query_params.get("lang"), allow_hinglish=True)
    if query_lang:
        return query_lang, True
    detected_lang = _normalize_lang(str(payload.get("detected_language") or ""), allow_hinglish=False)
    if detected_lang:
        return detected_lang, False
    header_lang = _lang_from_accept_header(request.headers.get("accept-language"))
    if header_lang:
        return header_lang, False
    return "en", False


def _hour_to_period(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 16:
        return "afternoon"
    return "evening"


def _period_label(period: str, lang: str) -> str:
    labels = {
        "en": {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"},
        "hi": {"morning": "सुबह", "afternoon": "दोपहर", "evening": "शाम"},
        "hinglish": {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"},
    }
    return labels.get(lang, labels["en"]).get(period, period.title())


def _normalize_time_value(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
        try:
            return datetime.strptime(text.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return text


def register_whatsapp_web_routes(
    app: Any,
    *,
    booking_repository: Any,
    scheduling_repository: Any,
    logger: Any,
) -> None:
    router = APIRouter()

    def _resolve_admin_id(doctor_id: int) -> int | None:
        if not booking_repository:
            return None
        conn = booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT admin_id
                FROM doctors
                WHERE doctor_id = %s
                LIMIT 1
                """,
                (int(doctor_id),),
            )
            row = cur.fetchone() or {}
            value = row.get("admin_id")
            return int(value) if value is not None else None
        finally:
            cur.close()
            conn.close()

    def _doctor_name(doctor_id: int, admin_id: int | None) -> str:
        if not booking_repository:
            return "Doctor"
        try:
            value = booking_repository.get_doctor_display_name(doctor_id=doctor_id, admin_id=admin_id)
            return str(value or "Doctor")
        except Exception:
            return "Doctor"

    def _resolve_doctor_id_by_slug(doctor_slug: str) -> int | None:
        if not booking_repository:
            return None
        slug = str(doctor_slug or "").strip()
        if not slug:
            return None
        conn = booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT doctor_id
                FROM doctors
                WHERE TRIM(COALESCE(slug, '')) = %s
                LIMIT 1
                """,
                (slug,),
            )
            row = cur.fetchone() or {}
            value = row.get("doctor_id")
            return int(value) if value is not None else None
        finally:
            cur.close()
            conn.close()

    def _format_time(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
            try:
                return datetime.strptime(text.upper(), fmt).strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
        return text

    def _max_active_bookings_message(lang: str) -> str:
        return get_message(lang, "max_active_bookings_reached")

    def _grouped_time_payload(
        times: list[str],
        lang: str,
        period: str | None = None,
        *,
        hour_value_mode: str = "exact",
    ) -> dict[str, object]:
        normalized_times = [_normalize_time_value(value) for value in (times or []) if _normalize_time_value(value)]
        hour_keys: list[str] = []
        seen: set[str] = set()
        for hhmm in normalized_times:
            hour = hhmm.split(":")[0]
            if hour not in seen:
                seen.add(hour)
                hour_keys.append(hour)
        if not hour_keys:
            return {"mode": "slots", "periods": [], "slots": []}
        if len(hour_keys) > 4 and not period:
            periods: list[str] = []
            for hour in hour_keys:
                current = _hour_to_period(int(hour))
                if current not in periods:
                    periods.append(current)
            return {
                "mode": "periods",
                "periods": [{"value": value, "label": _period_label(value, lang)} for value in periods],
                "slots": [],
            }
        hours = [int(hour) for hour in hour_keys]
        if period:
            hours = [hour for hour in hours if _hour_to_period(hour) == period]
        hours = sorted(hours)
        slots: list[dict[str, str]] = []
        for hour in hours:
            hour_times = [value for value in normalized_times if value.startswith(f"{hour:02d}:")]
            if not hour_times:
                continue
            start = f"{hour:02d}:00"
            end = f"{(hour + 1) % 24:02d}:00"
            slots.append(
                {
                    "value": f"hour:{hour:02d}" if hour_value_mode == "bucket" else hour_times[0],
                    "label": f"{_format_time(start)} - {_format_time(end)}",
                }
            )
        return {"mode": "slots", "periods": [], "slots": slots}

    def _reschedule_conflict_exists(
        *,
        doctor_id: int,
        appointment_id: int,
        slot_date: str,
        slot_time: str,
        admin_id: int | None,
    ) -> bool:
        if not booking_repository:
            return False
        normalized_time = _normalize_time_value(slot_time)
        if not normalized_time:
            return False
        conn = booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            appointment_table = booking_repository._appointment_table()
            params: list[object] = [int(doctor_id), str(slot_date), normalized_time, int(appointment_id)]
            cur.execute(
                f"""
                SELECT appointment_id
                FROM {appointment_table}
                WHERE doctor_id = %s
                  AND appointment_date = %s
                  AND TIME_FORMAT(start_time, '%H:%i') = %s
                  AND appointment_id <> %s
                LIMIT 1
                """,
                tuple(params),
            )
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    def _list_reschedule_exact_times(
        *,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        appointment_id: int,
        admin_id: int | None,
    ) -> list[str]:
        times = list(
            scheduling_repository._db_list_available_times_for_date(
                int(doctor_id),
                int(clinic_id),
                str(slot_date),
                admin_id,
            )
            or []
        )
        if not times:
            return []

        exact_times: list[str] = []
        for hhmm in times:
            if _reschedule_conflict_exists(
                doctor_id=int(doctor_id),
                appointment_id=int(appointment_id),
                slot_date=str(slot_date),
                slot_time=hhmm,
                admin_id=admin_id,
            ):
                continue
            exact_times.append(hhmm)
        return exact_times

    def _list_reschedule_times(
        *,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        appointment_id: int,
        admin_id: int | None,
        lang: str,
        period: str | None = None,
    ) -> dict[str, object]:
        exact_times = _list_reschedule_exact_times(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            appointment_id=appointment_id,
            admin_id=admin_id,
        )
        if not exact_times:
            return {"mode": "slots", "periods": [], "slots": []}
        return _grouped_time_payload(exact_times, lang, period, hour_value_mode="bucket")

    def _resolve_reschedule_time_choice(
        *,
        doctor_id: int,
        clinic_id: int,
        slot_date: str,
        appointment_id: int,
        selected_time: str,
        admin_id: int | None,
    ) -> str:
        normalized_choice = str(selected_time or "").strip().lower()
        exact_times = _list_reschedule_exact_times(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            appointment_id=appointment_id,
            admin_id=admin_id,
        )
        if not exact_times:
            return ""
        if normalized_choice.startswith("hour:"):
            hour_token = normalized_choice.split(":", 1)[1].strip()
            if len(hour_token) == 1:
                hour_token = f"0{hour_token}"
            for hhmm in exact_times:
                if hhmm.startswith(f"{hour_token}:"):
                    return hhmm
            return ""
        normalized_exact = _normalize_time_value(selected_time)
        if normalized_exact in exact_times:
            return normalized_exact
        return ""

    def _normalized_person_name(value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _find_self_name_by_phone_number(
        *,
        phone_number: str,
        doctor_id: int,
        admin_id: int | None,
    ) -> str:
        if not booking_repository:
            return ""
        target = booking_repository._normalize_phone(phone_number)
        actual_admin_id = admin_id or booking_repository.default_admin_id()
        if not target or not actual_admin_id:
            return ""

        phone_candidates: list[str] = []

        def _add_candidate(raw_value: str) -> None:
            value = str(raw_value or "").strip()
            if value and value not in phone_candidates:
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

        if not phone_candidates:
            return ""

        conn = booking_repository._connect()
        cur = conn.cursor(dictionary=True)
        try:
            placeholders = ", ".join(["%s"] * len(phone_candidates))
            cur.execute(
                f"""
                SELECT p.full_name
                FROM patients p
                WHERE p.admin_id = %s
                  AND p.doctor_id = %s
                  AND UPPER(COALESCE(p.profile_type, '')) = 'SELF'
                  AND COALESCE(p.phone, '') IN ({placeholders})
                ORDER BY p.patient_id DESC
                LIMIT 1
                """,
                tuple([actual_admin_id, int(doctor_id)] + phone_candidates),
            )
            row = cur.fetchone() or {}
            return str(row.get("full_name") or "").strip()
        finally:
            cur.close()
            conn.close()

    def _other_name_matches_self_name(
        *,
        patient_name: str,
        phone_number: str,
        doctor_id: int,
        admin_id: int | None,
    ) -> bool:
        if not booking_repository:
            return False
        normalized_candidate = _normalized_person_name(patient_name)
        if not normalized_candidate:
            return False
        known_name = _find_self_name_by_phone_number(
            phone_number=phone_number,
            doctor_id=doctor_id,
            admin_id=admin_id,
        )
        normalized_known = _normalized_person_name(str(known_name or ""))
        return bool(normalized_known and normalized_candidate == normalized_known)

    def _self_name_mismatch(
        *,
        patient_name: str,
        phone_number: str,
        doctor_id: int,
        admin_id: int | None,
    ) -> str:
        normalized_candidate = _normalized_person_name(patient_name)
        if not normalized_candidate:
            return ""
        known_name = _find_self_name_by_phone_number(
            phone_number=phone_number,
            doctor_id=doctor_id,
            admin_id=admin_id,
        )
        normalized_known = _normalized_person_name(known_name)
        if not normalized_known or normalized_candidate == normalized_known:
            return ""
        return known_name

    async def _require_repos(request: Request, payload: dict[str, object] | None = None) -> tuple[str, bool] | None:
        resolved_lang = _resolve_effective_language(request, payload)
        if booking_repository and scheduling_repository:
            return resolved_lang
        lang, _ = resolved_lang
        raise RuntimeError({"detail": "Booking repositories are not configured.", "lang": lang})

    async def _render_whatsapp_web_page(request: Request, doctor_id: int | None) -> HTMLResponse:
        resolved_lang, lock_language = _resolve_effective_language(request, {})
        if not doctor_id:
            return HTMLResponse("Missing doctor_id", status_code=400)
        if not booking_repository or not scheduling_repository:
            return HTMLResponse("Booking is not configured.", status_code=503)
        admin_id = await run_in_threadpool(_resolve_admin_id, int(doctor_id))
        doctor_name = await run_in_threadpool(_doctor_name, int(doctor_id), admin_id)
        return HTMLResponse(
            render_whatsapp_web_page_html(
                doctor_id=int(doctor_id),
                doctor_name=doctor_name,
                language=resolved_lang,
                lock_language=lock_language,
            )
        )

    @router.get("/whatsapp/web", response_class=HTMLResponse)
    async def whatsapp_web_page(request: Request, doctor_id: int | None = None):
        return await _render_whatsapp_web_page(request, doctor_id)

    @router.get("/whatsapp/web/clinics")
    async def whatsapp_web_clinics(request: Request, doctor_id: int | None = None):
        resolved_lang, _ = _resolve_effective_language(request, {})
        if not doctor_id:
            return JSONResponse({"detail": "Missing doctor_id."}, status_code=400)
        if not scheduling_repository:
            return JSONResponse({"detail": "Scheduling is not configured."}, status_code=503)
        admin_id = await run_in_threadpool(_resolve_admin_id, int(doctor_id))
        clinics = await run_in_threadpool(
            scheduling_repository.list_clinics_for_doctor,
            int(doctor_id),
            admin_id,
            20,
        )
        return JSONResponse(
            {
                "doctor_id": int(doctor_id),
                "language": resolved_lang,
                "clinics": [
                    {
                        "clinic_id": int(row.clinic_id),
                        "clinic_name": str(row.clinic_name or ""),
                        "location": str(row.location or ""),
                    }
                    for row in (clinics or [])
                ],
            }
        )

    @router.get("/whatsapp/web/dates")
    async def whatsapp_web_dates(request: Request, doctor_id: int | None = None, clinic_id: int | None = None):
        if not doctor_id or not clinic_id:
            return JSONResponse({"detail": "Missing doctor_id or clinic_id."}, status_code=400)
        if not scheduling_repository:
            return JSONResponse({"detail": "Scheduling is not configured."}, status_code=503)
        admin_id = await run_in_threadpool(_resolve_admin_id, int(doctor_id))
        dates = await run_in_threadpool(
            scheduling_repository.list_available_dates,
            int(doctor_id),
            int(clinic_id),
            admin_id,
            14,
        )
        return JSONResponse({"dates": list(dates or [])})

    @router.get("/whatsapp/web/times")
    async def whatsapp_web_times(
        request: Request,
        doctor_id: int | None = None,
        clinic_id: int | None = None,
        slot_date: str | None = None,
        period: str | None = None,
        appointment_id: int | None = None,
        reschedule: int | None = None,
    ):
        resolved_lang, _ = _resolve_effective_language(request, {})
        if not doctor_id or not clinic_id or not slot_date:
            return JSONResponse({"detail": "Missing doctor_id, clinic_id, or slot_date."}, status_code=400)
        if not scheduling_repository:
            return JSONResponse({"detail": "Scheduling is not configured."}, status_code=503)
        admin_id = await run_in_threadpool(_resolve_admin_id, int(doctor_id))
        if int(reschedule or 0) == 1 and appointment_id:
            payload = await run_in_threadpool(
                _list_reschedule_times,
                doctor_id=int(doctor_id),
                clinic_id=int(clinic_id),
                slot_date=str(slot_date),
                appointment_id=int(appointment_id),
                admin_id=admin_id,
                lang=resolved_lang,
                period=str(period or "").strip().lower() or None,
            )
            return JSONResponse(payload)
        times = await run_in_threadpool(
            scheduling_repository.list_available_times,
            int(doctor_id),
            int(clinic_id),
            str(slot_date),
            admin_id,
            60,
        )
        payload = _grouped_time_payload(list(times or []), resolved_lang, str(period or "").strip().lower() or None)
        return JSONResponse(payload)

    @router.post("/whatsapp/web/lookup")
    async def whatsapp_web_lookup(request: Request):
        payload = await request.json()
        resolved_lang, _ = _resolve_effective_language(request, payload)
        if not booking_repository:
            return JSONResponse({"detail": "Booking is not configured."}, status_code=503)
        try:
            doctor_id = int(payload.get("doctor_id") or 0)
        except Exception:
            doctor_id = 0
        patient_name = str(payload.get("patient_name") or "").strip()
        phone_number = str(payload.get("phone_number") or "").strip()
        booking_for_self = bool(payload.get("booking_for_self", True))
        if doctor_id <= 0 or not phone_number:
            return JSONResponse({"detail": "Missing doctor_id or phone_number."}, status_code=400)
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
        if booking_for_self and patient_name:
            self_name = await run_in_threadpool(
                _self_name_mismatch,
                patient_name=patient_name,
                phone_number=phone_number,
                doctor_id=doctor_id,
                admin_id=admin_id,
            )
            if self_name:
                return JSONResponse(
                    {
                        "detail": (
                            f"This phone number is linked to self name {self_name}. "
                            "Use that name for Self, or choose Someone Else."
                        )
                    },
                    status_code=400,
                )
        all_active_appointments = await run_in_threadpool(
            booking_repository.list_active_appointments_by_phone_number,
            phone_number,
            admin_id,
            doctor_id,
            10,
        )
        appointments = list(all_active_appointments or [])
        if not booking_for_self and patient_name:
            same_as_self = await run_in_threadpool(
                _other_name_matches_self_name,
                patient_name=patient_name,
                phone_number=phone_number,
                doctor_id=doctor_id,
                admin_id=admin_id,
            )
            if same_as_self:
                return JSONResponse(
                    {"detail": "Please use a different name other than self when booking for another person."},
                    status_code=400,
                )
        if not booking_for_self and patient_name:
            normalized_name = " ".join(patient_name.lower().split())
            matched_same_identity = []
            for row in (appointments or []):
                existing_name = " ".join(str(row.get("patient_name") or "").lower().split())
                if existing_name and existing_name == normalized_name:
                    matched_same_identity.append(row)
            if len(all_active_appointments or []) >= 2 and not matched_same_identity:
                return JSONResponse(
                    {"detail": _max_active_bookings_message(resolved_lang)},
                    status_code=400,
                )
            appointments = matched_same_identity
        return JSONResponse(
            {
                "language": resolved_lang,
                "appointments": [
                    {
                        "appointment_id": int(row.get("appointment_id") or 0),
                        "booking_number": row.get("booking_number"),
                        "clinic_id": row.get("clinic_id"),
                        "clinic_name": str(row.get("clinic_name") or ""),
                        "patient_name": str(row.get("patient_name") or ""),
                        "slot_date": str(row.get("slot_date") or ""),
                        "slot_time": _format_time(str(row.get("slot_time") or "")),
                        "doctor_id": row.get("doctor_id"),
                    }
                    for row in (appointments or [])
                ],
            }
        )

    @router.post("/whatsapp/web/book")
    async def whatsapp_web_book(request: Request):
        payload = await request.json()
        resolved_lang, _ = _resolve_effective_language(request, payload)
        if not booking_repository:
            return JSONResponse({"detail": "Booking is not configured."}, status_code=503)
        try:
            doctor_id = int(payload.get("doctor_id") or 0)
            clinic_id = int(payload.get("clinic_id") or 0)
        except Exception:
            return JSONResponse({"detail": "Invalid doctor_id or clinic_id."}, status_code=400)
        patient_name = str(payload.get("patient_name") or "").strip()
        phone_number = str(payload.get("phone_number") or "").strip()
        slot_date = str(payload.get("appointment_date") or "").strip()
        slot_time = _normalize_time_value(str(payload.get("appointment_time") or "").strip())
        booking_for_self = bool(payload.get("booking_for_self", True))
        if not all([doctor_id, clinic_id, patient_name, phone_number, slot_date, slot_time]):
            return JSONResponse({"detail": "Missing required booking fields."}, status_code=400)
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
        if booking_for_self:
            self_name = await run_in_threadpool(
                _self_name_mismatch,
                patient_name=patient_name,
                phone_number=phone_number,
                doctor_id=doctor_id,
                admin_id=admin_id,
            )
            if self_name:
                return JSONResponse(
                    {
                        "status": "error",
                        "message": (
                            f"This phone number is linked to self name {self_name}. "
                            "Use that name for Self, or choose Someone Else."
                        ),
                    },
                    status_code=400,
                )
        active_phone_appointments = await run_in_threadpool(
            booking_repository.list_active_appointments_by_phone_number,
            phone_number,
            admin_id,
            doctor_id,
            10,
        )
        if len(active_phone_appointments or []) >= 2:
            return JSONResponse(
                {
                    "status": "error",
                    "message": _max_active_bookings_message(resolved_lang),
                },
                status_code=400,
            )
        if not booking_for_self:
            same_as_self = await run_in_threadpool(
                _other_name_matches_self_name,
                patient_name=patient_name,
                phone_number=phone_number,
                doctor_id=doctor_id,
                admin_id=admin_id,
            )
            if same_as_self:
                return JSONResponse(
                    {
                        "status": "error",
                        "message": "Please use a different name other than self when booking for another person.",
                    },
                    status_code=400,
                )
        if booking_for_self:
            existing = await run_in_threadpool(
                booking_repository.find_active_appointment_by_phone_number,
                phone_number,
                admin_id,
                doctor_id,
            )
            if existing:
                booking_number = existing.get("booking_number") or existing.get("appointment_id")
                return JSONResponse(
                    {
                        "status": "active_booking",
                        "message": (
                            f"You already have an active appointment.\n"
                            f"Appointment ID: {booking_number}\n"
                            f"Date: {existing.get('slot_date') or '-'}\n"
                            f"Time: {_format_time(str(existing.get('slot_time') or '')) or '-'}"
                        ),
                    },
                    status_code=400,
                )
        context = SimpleNamespace(
            patient_name=patient_name,
            phone_number=phone_number,
            clinic_id=str(clinic_id),
            appointment_date=slot_date,
            appointment_time=slot_time,
            reason="WhatsApp Web Booking",
            appointment_mode="whatsapp-web",
            booking_channel="whatsapp_web",
            booking_for_self=booking_for_self,
            chat_user_id=None,
            age=None,
            gender=None,
            patient_type="existing",
        )
        result = await run_in_threadpool(
            booking_repository.save_confirmed_appointment,
            context,
            admin_id,
            doctor_id,
        )
        if result.ok:
            booking_number = result.queue_number if result.queue_number is not None else result.appointment_id
            return JSONResponse(
                {
                    "status": "booked",
                    "message": f"Appointment booked successfully.\nAppointment ID: {booking_number}",
                    "appointment_id": result.appointment_id,
                    "booking_number": booking_number,
                }
            )
        existing = await run_in_threadpool(
            booking_repository.find_active_appointment_by_phone_number,
            phone_number,
            admin_id,
            doctor_id,
        )
        if existing:
            booking_number = existing.get("booking_number") or existing.get("appointment_id")
            return JSONResponse(
                {
                    "status": "active_booking",
                        "message": (
                            f"You already have an active appointment.\n"
                            f"Appointment ID: {booking_number}\n"
                            f"Date: {existing.get('slot_date') or '-'}\n"
                            f"Time: {_format_time(str(existing.get('slot_time') or '')) or '-'}"
                        ),
                }
            )
        return JSONResponse({"status": "error", "message": str(result.message or "Unable to book appointment.")}, status_code=400)

    @router.post("/whatsapp/web/cancel")
    async def whatsapp_web_cancel(request: Request):
        payload = await request.json()
        resolved_lang, _ = _resolve_effective_language(request, payload)
        if not booking_repository:
            return JSONResponse({"detail": "Booking is not configured."}, status_code=503)
        try:
            doctor_id = int(payload.get("doctor_id") or 0)
            appointment_id = int(payload.get("appointment_id") or 0)
        except Exception:
            return JSONResponse({"detail": "Invalid doctor_id or appointment_id."}, status_code=400)
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
        ok = await run_in_threadpool(
            booking_repository.cancel_appointment,
            appointment_id,
            admin_id,
            "PATIENT",
        )
        if not ok:
            return JSONResponse({"status": "error", "message": "Unable to cancel appointment."}, status_code=400)
        return JSONResponse({"status": "ok", "message": "Appointment cancelled successfully."})

    @router.post("/whatsapp/web/reschedule")
    async def whatsapp_web_reschedule(request: Request):
        payload = await request.json()
        resolved_lang, _ = _resolve_effective_language(request, payload)
        if not booking_repository:
            return JSONResponse({"detail": "Booking is not configured."}, status_code=503)
        try:
            doctor_id = int(payload.get("doctor_id") or 0)
            appointment_id = int(payload.get("appointment_id") or 0)
            clinic_id = int(payload.get("clinic_id") or 0)
        except Exception:
            return JSONResponse({"detail": "Invalid doctor_id, appointment_id, or clinic_id."}, status_code=400)
        slot_date = str(payload.get("appointment_date") or "").strip()
        submitted_time = str(payload.get("appointment_time") or "").strip()
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
        slot_time = await run_in_threadpool(
            _resolve_reschedule_time_choice,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            appointment_id=appointment_id,
            selected_time=submitted_time,
            admin_id=admin_id,
        )
        if not slot_time:
            return JSONResponse(
                {"status": "error", "message": "Selected hour is full now. Please choose another slot."},
                status_code=400,
            )
        conflict_exists = await run_in_threadpool(
            _reschedule_conflict_exists,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            slot_date=slot_date,
            slot_time=slot_time,
            admin_id=admin_id,
        )
        if conflict_exists:
            return JSONResponse(
                {"status": "error", "message": "Selected time is already booked. Please choose another slot."},
                status_code=400,
            )
        result = await run_in_threadpool(
            booking_repository.reschedule_appointment_same_clinic,
            appointment_id,
            slot_date,
            slot_time,
            clinic_id,
            admin_id,
            "PATIENT",
        )
        if not result.ok:
            return JSONResponse({"status": "error", "message": str(result.message or "Unable to reschedule appointment.")}, status_code=400)
        booking_number = result.queue_number if result.queue_number is not None else result.appointment_id
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Appointment rescheduled successfully.\nAppointment ID: {booking_number}",
                "appointment_id": result.appointment_id,
                "booking_number": booking_number,
            }
        )

    @router.get("/whatsapp/web/{doctor_slug}", response_class=HTMLResponse)
    async def whatsapp_web_page_by_slug(request: Request, doctor_slug: str):
        doctor_id = await run_in_threadpool(_resolve_doctor_id_by_slug, doctor_slug)
        if not doctor_id:
            return HTMLResponse("Doctor slug not found", status_code=404)
        return await _render_whatsapp_web_page(request, doctor_id)

    app.include_router(router)
