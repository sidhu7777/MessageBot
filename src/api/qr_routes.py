import asyncio
import os
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from src.messages.templates import get_qr_message
from src.qr.page_renderer import render_qr_page_html


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
    if allow_hinglish and raw == "hinglish":
        return "hinglish"
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


def register_qr_routes(
    app: Any,
    *,
    qr_checkin_service: Any,
    logger: Any,
    log_event_fn: Callable[..., None],
) -> None:
    router = APIRouter()
    qr_page_lookup_timeout_seconds = max(
        0.2, float(os.getenv("QR_PAGE_LOOKUP_TIMEOUT_SECONDS", "2.0"))
    )

    @router.get("/qr/checkin", response_class=HTMLResponse)
    async def qr_checkin_page(request: Request, doctor_id: int | None = None, clinic_id: int | None = None):
        resolved_lang, lock_language = _resolve_effective_language(request, {})

        if not qr_checkin_service:
            return HTMLResponse(get_qr_message(resolved_lang, "qr_not_configured"), status_code=503)
        if not doctor_id or not clinic_id:
            return HTMLResponse(get_qr_message(resolved_lang, "qr_missing_params"), status_code=400)

        doctor_name, clinic_name = "Doctor", "Clinic"
        try:
            doctor_name, clinic_name = await asyncio.wait_for(
                run_in_threadpool(
                    qr_checkin_service.resolve_doctor_and_clinic,
                    doctor_id=doctor_id,
                    clinic_id=clinic_id,
                ),
                timeout=qr_page_lookup_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "QR page name lookup fallback doctor_id=%s clinic_id=%s error=%s",
                doctor_id,
                clinic_id,
                exc,
            )

        return HTMLResponse(
            render_qr_page_html(
                doctor_id=doctor_id,
                clinic_id=clinic_id,
                doctor_name=doctor_name,
                clinic_name=clinic_name,
                language=resolved_lang,
                lock_language=lock_language,
            )
        )

    @router.post("/qr/checkin/submit")
    async def qr_checkin_submit(request: Request):
        if not qr_checkin_service:
            fallback_lang, _ = _resolve_effective_language(request, {})
            msg = get_qr_message(fallback_lang, "qr_not_configured_plain")
            return JSONResponse({"detail": msg, "message": msg}, status_code=503)

        payload: dict[str, object] = {}
        try:
            payload = await request.json()
        except Exception:
            try:
                form = await request.form()
                payload = dict(form)
            except Exception:
                resolved_lang, _ = _resolve_effective_language(request, {})
                msg = get_qr_message(resolved_lang, "qr_invalid_payload")
                return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        resolved_lang, lock_language = _resolve_effective_language(request, payload)

        patient_name = str(payload.get("patient_name") or "").strip()
        phone_number = str(payload.get("phone_number") or "").strip()
        qr_chat_id = f"qr_{''.join(ch for ch in phone_number if ch.isdigit()) or 'unknown'}"
        log_event_fn(
            qr_chat_id,
            "QR_SUBMIT_RECEIVED",
            doctor_id=payload.get("doctor_id"),
            clinic_id=payload.get("clinic_id"),
            patient_name=patient_name[:80],
            phone=phone_number,
            response_language=resolved_lang,
        )
        try:
            doctor_raw = payload.get("doctor_id") or request.query_params.get("doctor_id")
            clinic_raw = payload.get("clinic_id") or request.query_params.get("clinic_id")
            doctor_id = int(doctor_raw)
            clinic_id = int(clinic_raw)
        except Exception:
            log_event_fn(
                qr_chat_id,
                "QR_SUBMIT_REJECTED",
                reason="invalid_doctor_or_clinic_id",
                doctor_id=payload.get("doctor_id"),
                clinic_id=payload.get("clinic_id"),
            )
            msg = get_qr_message(resolved_lang, "qr_invalid_ids")
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        result = await run_in_threadpool(
            qr_checkin_service.process_checkin,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            patient_name=patient_name,
            phone=phone_number,
            language=resolved_lang,
        )
        status_code = (
            200 if result.status in {"booked", "overflow", "active_booking"} else 400
        )
        event_name = "QR_SUBMIT_SUCCEEDED" if status_code == 200 else "QR_SUBMIT_FAILED"
        log_event_fn(
            qr_chat_id,
            event_name,
            status=result.status,
            message=result.message[:120],
            booking_id=result.booking_id,
            appointment_date=result.appointment_date,
            appointment_time=result.appointment_time,
            clinic_name=result.clinic_name,
            doctor_name=result.doctor_name,
            response_language=resolved_lang,
        )
        if status_code != 200:
            logger.warning(
                "QR submit failed doctor_id=%s clinic_id=%s phone=%s status=%s message=%s",
                doctor_id,
                clinic_id,
                phone_number,
                result.status,
                result.message,
            )
        accept_header = (request.headers.get("accept") or "").lower()
        if "text/html" in accept_header and "application/json" not in accept_header:
            return HTMLResponse(
                render_qr_page_html(
                    doctor_id=doctor_id,
                    clinic_id=clinic_id,
                    doctor_name=result.doctor_name or "Doctor",
                    clinic_name=result.clinic_name or "Clinic",
                    result_message=result.message,
                    result_status=result.status,
                    patient_name=patient_name,
                    phone_number=phone_number,
                    language=resolved_lang,
                    lock_language=lock_language,
                ),
                status_code=status_code,
            )
        return JSONResponse(
            {
                "status": result.status,
                "message": result.message,
                "detail": result.message,
                "booking_id": result.booking_id,
                "appointment_date": result.appointment_date,
                "appointment_time": result.appointment_time,
                "queue_position": result.queue_position,
                "estimated_time": result.estimated_time,
                "clinic_name": result.clinic_name,
                "doctor_name": result.doctor_name,
                "response_language": resolved_lang,
            },
            status_code=status_code,
        )

    app.include_router(router)
