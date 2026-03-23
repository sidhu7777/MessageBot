from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

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

    def _grouped_time_payload(times: list[str], lang: str, period: str | None = None) -> dict[str, object]:
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
                    "value": hour_times[0],
                    "label": f"{_format_time(start)} - {_format_time(end)}",
                }
            )
        return {"mode": "slots", "periods": [], "slots": slots}

    async def _require_repos(request: Request, payload: dict[str, object] | None = None) -> tuple[str, bool] | None:
        resolved_lang = _resolve_effective_language(request, payload)
        if booking_repository and scheduling_repository:
            return resolved_lang
        lang, _ = resolved_lang
        raise RuntimeError({"detail": "Booking repositories are not configured.", "lang": lang})

    @router.get("/whatsapp/web", response_class=HTMLResponse)
    async def whatsapp_web_page(request: Request, doctor_id: int | None = None):
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
    ):
        resolved_lang, _ = _resolve_effective_language(request, {})
        if not doctor_id or not clinic_id or not slot_date:
            return JSONResponse({"detail": "Missing doctor_id, clinic_id, or slot_date."}, status_code=400)
        if not scheduling_repository:
            return JSONResponse({"detail": "Scheduling is not configured."}, status_code=503)
        admin_id = await run_in_threadpool(_resolve_admin_id, int(doctor_id))
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
        phone_number = str(payload.get("phone_number") or "").strip()
        if doctor_id <= 0 or not phone_number:
            return JSONResponse({"detail": "Missing doctor_id or phone_number."}, status_code=400)
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
        appointments = await run_in_threadpool(
            booking_repository.list_active_appointments_by_phone_number,
            phone_number,
            admin_id,
            doctor_id,
            10,
        )
        return JSONResponse(
            {
                "language": resolved_lang,
                "appointments": [
                    {
                        "appointment_id": int(row.get("appointment_id") or 0),
                        "booking_number": row.get("booking_number"),
                        "clinic_id": row.get("clinic_id"),
                        "clinic_name": str(row.get("clinic_name") or ""),
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
        slot_time = _normalize_time_value(str(payload.get("appointment_time") or "").strip())
        admin_id = await run_in_threadpool(_resolve_admin_id, doctor_id)
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

    app.include_router(router)
