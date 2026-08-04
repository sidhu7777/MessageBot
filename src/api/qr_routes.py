import asyncio
import os
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.messages.templates import get_qr_message
from src.qr.generator_service import (
    build_download_filename,
    build_hospital_download_filename,
    build_qr_booking_url,
    build_qr_hospital_registration_url,
    build_qr_hospital_url,
    build_qr_svg,
    svg_to_data_url,
)
from src.qr.page_renderer import (
    render_hospital_qr_page_html,
    render_hospital_registration_page_html,
    render_qr_page_html,
)


_SUPPORTED_LANGS = {"en", "hi", "hinglish"}

# --- Hospital REGISTRATION flow config -----------------------------------------
# Doctor + specialization are read from the DB (same lookup as the hospital page),
# keyed by hospital_code. Submit does NOT book an appointment and does NOT write to
# the DB — it only returns a unique token (Redis daily counter). Fallback name below
# is used only when the DB has no name for the code.
REGISTRATION_HOSPITAL_NAME_DEFAULT = "Nirmal Ashram Hospital"

# Localized registration error messages (English + Hindi) so the patient sees the REAL
# reason in their language instead of a generic "unable to submit".
REGISTRATION_ERRORS = {
    "missing_fields": {
        "en": "Please fill name, age, gender and phone number.",
        "hi": "कृपया नाम, उम्र, लिंग और फ़ोन नंबर भरें।",
    },
    "invalid_contact": {
        "en": "Please enter a valid name and a 10-digit phone number.",
        "hi": "कृपया सही नाम और 10 अंकों का फ़ोन नंबर दर्ज करें।",
    },
    "hospital_not_configured": {
        "en": "This hospital is not available for registration right now.",
        "hi": "यह अस्पताल अभी पंजीकरण के लिए उपलब्ध नहीं है।",
    },
    "not_configured": {
        "en": "Registration is not available right now.",
        "hi": "पंजीकरण अभी उपलब्ध नहीं है।",
    },
    "failed": {
        "en": "Unable to register right now. Please try again.",
        "hi": "अभी पंजीकरण नहीं हो सका। कृपया पुनः प्रयास करें।",
    },
}


def _registration_error(language: str, code: str) -> str:
    entry = REGISTRATION_ERRORS.get(code, REGISTRATION_ERRORS["failed"])
    return entry.get("hi" if language == "hi" else "en", entry["en"])


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

    @router.get("/qr/hospital/checkin", response_class=HTMLResponse)
    async def qr_hospital_checkin_page(request: Request, hospital_code: str = "", hospital_group_code: str = ""):
        resolved_lang, lock_language = _resolve_effective_language(request, {})
        code = (hospital_code or hospital_group_code or "").strip()
        if not qr_checkin_service:
            return HTMLResponse(get_qr_message(resolved_lang, "qr_not_configured"), status_code=503)
        if not code:
            return HTMLResponse("<h3>Hospital code is required.</h3>", status_code=400)
        try:
            options = await asyncio.wait_for(
                run_in_threadpool(qr_checkin_service.hospital_qr_options, code),
                timeout=max(qr_page_lookup_timeout_seconds, 15.0),
            )
        except Exception as exc:
            logger.warning("Hospital QR options lookup failed hospital_code=%s error=%s", code, exc)
            options = {"hospital_code": code, "specializations": [], "doctors": []}
        return HTMLResponse(
            render_hospital_qr_page_html(
                hospital_code=code,
                options=options,
                language=resolved_lang,
                lock_language=lock_language,
            )
        )

    @router.get("/qr/hospital/options")
    async def qr_hospital_options(hospital_code: str = "", hospital_group_code: str = ""):
        if not qr_checkin_service:
            return JSONResponse({"detail": "QR check-in is not configured."}, status_code=503)
        code = (hospital_code or hospital_group_code or "").strip()
        if not code:
            return JSONResponse({"detail": "Hospital code is required."}, status_code=400)
        try:
            return JSONResponse(qr_checkin_service.hospital_qr_options(code))
        except Exception as exc:
            logger.warning("Hospital QR options failed hospital_code=%s error=%s", code, exc)
            return JSONResponse({"detail": "Hospital doctors are not available right now."}, status_code=400)

    @router.post("/qr/hospital/checkin/submit")
    async def qr_hospital_checkin_submit(request: Request):
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
        hospital_code = str(
            payload.get("hospital_code")
            or payload.get("hospital_group_code")
            or request.query_params.get("hospital_code")
            or request.query_params.get("hospital_group_code")
            or ""
        ).strip()
        patient_name = str(payload.get("patient_name") or "").strip()
        phone_number = str(payload.get("phone_number") or "").strip()
        qr_chat_id = f"qr_hospital_{''.join(ch for ch in phone_number if ch.isdigit()) or 'unknown'}"
        try:
            doctor_id = int(payload.get("doctor_id") or request.query_params.get("doctor_id"))
        except Exception:
            log_event_fn(
                qr_chat_id,
                "QR_HOSPITAL_SUBMIT_REJECTED",
                reason="invalid_doctor_id",
                hospital_code=hospital_code,
                doctor_id=payload.get("doctor_id"),
            )
            return JSONResponse({"detail": "Please choose a valid doctor.", "message": "Please choose a valid doctor."}, status_code=400)
        if not hospital_code:
            return JSONResponse({"detail": "Hospital code is required.", "message": "Hospital code is required."}, status_code=400)

        log_event_fn(
            qr_chat_id,
            "QR_HOSPITAL_SUBMIT_RECEIVED",
            hospital_code=hospital_code,
            doctor_id=doctor_id,
            patient_name=patient_name[:80],
            phone=phone_number,
            response_language=resolved_lang,
        )
        schedule = await run_in_threadpool(
            qr_checkin_service.resolve_hospital_qr_schedule,
            hospital_code=hospital_code,
            doctor_id=doctor_id,
        )
        if not schedule:
            message = "No valid doctor schedule is available for this hospital right now."
            log_event_fn(
                qr_chat_id,
                "QR_HOSPITAL_SUBMIT_REJECTED",
                reason="schedule_not_found",
                hospital_code=hospital_code,
                doctor_id=doctor_id,
            )
            return JSONResponse({"status": "error", "detail": message, "message": message}, status_code=400)

        result = await run_in_threadpool(
            qr_checkin_service.process_checkin,
            doctor_id=schedule.doctor_id,
            clinic_id=schedule.clinic_id,
            patient_name=patient_name,
            phone=phone_number,
            language=resolved_lang,
        )
        status_code = 200 if result.status in {"booked", "overflow", "active_booking"} else 400
        event_name = "QR_HOSPITAL_SUBMIT_SUCCEEDED" if status_code == 200 else "QR_HOSPITAL_SUBMIT_FAILED"
        log_event_fn(
            qr_chat_id,
            event_name,
            status=result.status,
            hospital_code=hospital_code,
            doctor_id=schedule.doctor_id,
            clinic_id=schedule.clinic_id,
            message=result.message[:120],
            booking_id=result.booking_id,
            appointment_date=result.appointment_date,
            appointment_time=result.appointment_time,
            clinic_name=result.clinic_name,
            doctor_name=result.doctor_name,
            response_language=resolved_lang,
        )
        accept_header = (request.headers.get("accept") or "").lower()
        if "text/html" in accept_header and "application/json" not in accept_header:
            try:
                options = qr_checkin_service.hospital_qr_options(hospital_code)
            except Exception:
                options = {"hospital_code": hospital_code, "specializations": [], "doctors": []}
            return HTMLResponse(
                render_hospital_qr_page_html(
                    hospital_code=hospital_code,
                    options=options,
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
                "clinic_id": schedule.clinic_id,
                "clinic_name": result.clinic_name or schedule.clinic_name,
                "doctor_id": schedule.doctor_id,
                "doctor_name": result.doctor_name or schedule.doctor_name,
                "specialization": schedule.specialization,
                "hospital_code": hospital_code,
                "response_language": resolved_lang,
            },
            status_code=status_code,
        )

    @router.post("/qr/generate")
    async def qr_generate(request: Request):
        payload: dict[str, object] = {}
        try:
            payload = await request.json()
        except Exception:
            try:
                form = await request.form()
                payload = dict(form)
            except Exception:
                payload = {}

        resolved_lang, _ = _resolve_effective_language(request, payload)
        try:
            doctor_raw = payload.get("doctor_id") or request.query_params.get("doctor_id")
            clinic_raw = payload.get("clinic_id") or request.query_params.get("clinic_id")
            doctor_id = int(doctor_raw)
            clinic_id = int(clinic_raw)
        except Exception:
            msg = get_qr_message(resolved_lang, "qr_invalid_ids")
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        doctor_name, clinic_name = "Doctor", "Clinic"
        if qr_checkin_service:
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
                    "QR generator name lookup fallback doctor_id=%s clinic_id=%s error=%s",
                    doctor_id,
                    clinic_id,
                    exc,
                )

        booking_url = build_qr_booking_url(
            base_url=str(request.base_url),
            doctor_id=doctor_id,
            clinic_id=clinic_id,
        )
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_download_filename(
            doctor_name=doctor_name,
            clinic_name=clinic_name,
            doctor_id=doctor_id,
            clinic_id=clinic_id,
        )
        return JSONResponse(
            {
                "doctor_id": doctor_id,
                "clinic_id": clinic_id,
                "doctor_name": doctor_name,
                "clinic_name": clinic_name,
                "mime_type": "image/svg+xml",
                "filename": filename,
                "preview_data_url": svg_to_data_url(svg_markup),
                "download_path": f"/qr/generate/download?doctor_id={doctor_id}&clinic_id={clinic_id}",
            }
        )

    @router.get("/qr/generate/download")
    async def qr_generate_download(request: Request, doctor_id: int | None = None, clinic_id: int | None = None):
        resolved_lang, _ = _resolve_effective_language(request, {})
        if not doctor_id or not clinic_id:
            msg = get_qr_message(resolved_lang, "qr_invalid_ids")
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        doctor_name, clinic_name = "Doctor", "Clinic"
        if qr_checkin_service:
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
                    "QR generator download name lookup fallback doctor_id=%s clinic_id=%s error=%s",
                    doctor_id,
                    clinic_id,
                    exc,
                )

        booking_url = build_qr_booking_url(
            base_url=str(request.base_url),
            doctor_id=int(doctor_id),
            clinic_id=int(clinic_id),
        )
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_download_filename(
            doctor_name=doctor_name,
            clinic_name=clinic_name,
            doctor_id=int(doctor_id),
            clinic_id=int(clinic_id),
        )
        return Response(
            content=svg_markup,
            media_type="image/svg+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def _resolve_hospital_display_name(code: str) -> str:
        if not qr_checkin_service:
            return "Hospital"
        try:
            name = await asyncio.wait_for(
                run_in_threadpool(qr_checkin_service.hospital_qr_display_name, code),
                timeout=qr_page_lookup_timeout_seconds,
            )
            return str(name or "").strip() or "Hospital"
        except Exception as exc:
            logger.warning("Hospital QR generator name lookup fallback hospital_code=%s error=%s", code, exc)
            return "Hospital"

    @router.post("/qr/hospital/generate")
    async def qr_hospital_generate(request: Request):
        payload: dict[str, object] = {}
        try:
            payload = await request.json()
        except Exception:
            try:
                form = await request.form()
                payload = dict(form)
            except Exception:
                payload = {}

        resolved_lang, _ = _resolve_effective_language(request, payload)
        code = str(
            payload.get("hospital_code")
            or payload.get("hospital_group_code")
            or request.query_params.get("hospital_code")
            or request.query_params.get("hospital_group_code")
            or ""
        ).strip()
        if not code:
            msg = "Hospital code is required."
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        hospital_name = await _resolve_hospital_display_name(code)
        booking_url = build_qr_hospital_url(base_url=str(request.base_url), hospital_code=code)
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_hospital_download_filename(hospital_name=hospital_name, hospital_code=code)
        return JSONResponse(
            {
                "hospital_code": code,
                "hospital_name": hospital_name,
                "mime_type": "image/svg+xml",
                "filename": filename,
                "preview_data_url": svg_to_data_url(svg_markup),
                "download_path": f"/qr/hospital/generate/download?hospital_code={code}",
            }
        )

    @router.get("/qr/hospital/generate/download")
    async def qr_hospital_generate_download(request: Request, hospital_code: str = "", hospital_group_code: str = ""):
        code = (hospital_code or hospital_group_code or "").strip()
        if not code:
            msg = "Hospital code is required."
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)

        hospital_name = await _resolve_hospital_display_name(code)
        booking_url = build_qr_hospital_url(base_url=str(request.base_url), hospital_code=code)
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_hospital_download_filename(hospital_name=hospital_name, hospital_code=code)
        return Response(
            content=svg_markup,
            media_type="image/svg+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # --- Hospital REGISTRATION flow (static, NO DB, isolated from all above) ------
    @router.get("/qr/hospital/registration/checkin", response_class=HTMLResponse)
    async def qr_hospital_registration_page(request: Request, hospital_code: str = "", hospital_name: str = ""):
        resolved_lang, _ = _resolve_effective_language(request, {})
        code = (hospital_code or "").strip()
        options = {"hospital_code": code, "hospital_name": "", "specializations": [], "doctors": []}
        if qr_checkin_service and code:
            try:
                options = await asyncio.wait_for(
                    run_in_threadpool(qr_checkin_service.hospital_qr_options, code),
                    timeout=max(qr_page_lookup_timeout_seconds, 15.0),
                )
            except Exception as exc:
                logger.warning("Hospital registration options lookup failed hospital_code=%s error=%s", code, exc)
                options = {"hospital_code": code, "hospital_name": "", "specializations": [], "doctors": []}
        name = (
            (hospital_name or "").strip()
            or str(options.get("hospital_name") or "").strip()
            or REGISTRATION_HOSPITAL_NAME_DEFAULT
        )
        return HTMLResponse(
            render_hospital_registration_page_html(
                hospital_code=code,
                hospital_name=name,
                doctors=options.get("doctors") or [],
                specializations=options.get("specializations") or [],
                language=resolved_lang,
            )
        )

    @router.post("/qr/hospital/registration/submit")
    async def qr_hospital_registration_submit(request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        patient_name = str(payload.get("patient_name") or "").strip()
        age = str(payload.get("age") or "").strip()
        gender = str(payload.get("gender") or "").strip()
        phone_number = str(payload.get("phone_number") or "").strip()
        doctor_name = str(payload.get("doctor_name") or "").strip()
        specialization = str(payload.get("specialization") or "").strip()
        hospital_code = str(payload.get("hospital_code") or "").strip()
        resolved_lang, _ = _resolve_effective_language(request, payload)
        try:
            doctor_id = int(payload.get("doctor_id"))
        except Exception:
            doctor_id = 0

        if not patient_name or not age or not gender or not phone_number:
            msg = _registration_error(resolved_lang, "missing_fields")
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)
        # doctor_id is OPTIONAL for registration (0 -> no doctor, stored as NULL)
        if not qr_checkin_service:
            msg = _registration_error(resolved_lang, "not_configured")
            return JSONResponse({"detail": msg, "message": msg}, status_code=503)

        # Insert into the dedicated hospital_registrations table (never touches patients).
        # Daily-unique token via DB sequence + UNIQUE constraints; one registration per
        # (hospital + phone) per day.
        try:
            result = await run_in_threadpool(
                qr_checkin_service.register_hospital_patient,
                hospital_code=hospital_code,
                doctor_id=doctor_id,
                patient_name=patient_name,
                phone=phone_number,
                age=age,
                gender=gender,
            )
        except Exception as exc:
            logger.warning("Hospital registration failed hospital_code=%s error=%s", hospital_code, exc)
            msg = _registration_error(resolved_lang, "failed")
            return JSONResponse({"detail": msg, "message": msg}, status_code=500)

        status = str(result.get("status") or "")
        qr_chat_id = f"qr_registration_{''.join(ch for ch in phone_number if ch.isdigit()) or 'unknown'}"
        log_event_fn(
            qr_chat_id,
            "QR_HOSPITAL_REGISTRATION_SUBMIT",
            hospital_code=hospital_code,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            specialization=specialization,
            patient_name=patient_name[:80],
            age=age,
            gender=gender,
            token=result.get("token"),
            result_status=status,
        )
        if status == "error":
            code = str(result.get("code") or "failed")
            msg = _registration_error(resolved_lang, code)
            return JSONResponse({"detail": msg, "message": msg}, status_code=400)
        return JSONResponse(result)

    @router.post("/qr/hospital/registration/generate")
    async def qr_hospital_registration_generate(request: Request):
        payload: dict[str, object] = {}
        try:
            payload = await request.json()
        except Exception:
            try:
                form = await request.form()
                payload = dict(form)
            except Exception:
                payload = {}

        code = str(
            payload.get("hospital_code")
            or request.query_params.get("hospital_code")
            or ""
        ).strip()
        name = str(
            payload.get("hospital_name")
            or request.query_params.get("hospital_name")
            or ""
        ).strip() or REGISTRATION_HOSPITAL_NAME_DEFAULT

        booking_url = build_qr_hospital_registration_url(
            base_url=str(request.base_url), hospital_code=code, hospital_name=name
        )
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_hospital_download_filename(hospital_name=f"{name}-registration", hospital_code=code or "registration")
        download_query = f"hospital_code={code}&hospital_name={name}"
        return JSONResponse(
            {
                "hospital_code": code,
                "hospital_name": name,
                "mime_type": "image/svg+xml",
                "filename": filename,
                "preview_data_url": svg_to_data_url(svg_markup),
                "download_path": f"/qr/hospital/registration/generate/download?{download_query}",
            }
        )

    @router.get("/qr/hospital/registration/generate/download")
    async def qr_hospital_registration_generate_download(request: Request, hospital_code: str = "", hospital_name: str = ""):
        code = (hospital_code or "").strip()
        name = (hospital_name or "").strip() or REGISTRATION_HOSPITAL_NAME_DEFAULT
        booking_url = build_qr_hospital_registration_url(
            base_url=str(request.base_url), hospital_code=code, hospital_name=name
        )
        svg_markup = await run_in_threadpool(build_qr_svg, url=booking_url)
        filename = build_hospital_download_filename(hospital_name=f"{name}-registration", hospital_code=code or "registration")
        return Response(
            content=svg_markup,
            media_type="image/svg+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.include_router(router)
