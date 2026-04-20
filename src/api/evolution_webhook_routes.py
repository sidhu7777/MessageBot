from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from src.chat_logger import log_event


def _route_path(url_or_path: str, default_path: str) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return default_path
    parsed = urlparse(raw)
    candidate = parsed.path if parsed.scheme and parsed.netloc else raw
    candidate = candidate.strip()
    if not candidate:
        return default_path
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate


def register_evolution_webhook_routes(
    app,
    *,
    settings,
    logger,
    evolution_repository,
    evolution_policy,
    evolution_api_client,
) -> None:
    router = APIRouter()
    webhook_path = _route_path(getattr(settings, "evolution_webhook_url", ""), "/evolution/webhook")
    configured_secret = str(getattr(settings, "evolution_webhook_secret", "") or "").strip()

    def _chat_log_id(remote_jid: str) -> str:
        raw = str(remote_jid or "").strip()
        if not raw:
            return "evolution_unknown"
        return f"evolution_{raw.split('@', 1)[0].replace(':', '_')}"

    def _event_name(payload: dict) -> str:
        return str(
            payload.get("event")
            or payload.get("eventName")
            or payload.get("type")
            or ""
        ).strip()

    def _extract_message_event(payload: dict) -> dict[str, str] | None:
        if not isinstance(payload, dict):
            return None
        event_name = _event_name(payload).lower()
        if event_name and "message" not in event_name:
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        key = data.get("key") if isinstance(data.get("key"), dict) else {}
        if bool(key.get("fromMe")):
            return None
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        text = ""
        if isinstance(message.get("conversation"), str):
            text = message.get("conversation") or ""
        elif isinstance(message.get("extendedTextMessage"), dict):
            text = str(message.get("extendedTextMessage", {}).get("text") or "")
        elif isinstance(data.get("text"), str):
            text = data.get("text") or ""
        remote_jid = str(
            key.get("remoteJid")
            or data.get("remoteJid")
            or data.get("from")
            or payload.get("from")
            or ""
        ).strip()
        instance_name = str(
            payload.get("instance")
            or payload.get("instanceName")
            or data.get("instance")
            or data.get("instanceName")
            or ""
        ).strip()
        if not remote_jid or not text.strip():
            return None
        return {
            "instance_name": instance_name,
            "remote_jid": remote_jid,
            "text": text.strip(),
        }

    def _build_booking_link(slug: str) -> str:
        base_url = str(
            getattr(settings, "evolution_booking_base_url", "")
            or os.getenv("QR_BASE_URL", "")
            or ""
        ).strip().rstrip("/")
        path_prefix = str(getattr(settings, "evolution_booking_path_prefix", "/whatsapp/web") or "/whatsapp/web").strip()
        path_prefix = path_prefix if path_prefix.startswith("/") else f"/{path_prefix}"
        path_prefix = path_prefix.rstrip("/")
        if not base_url or not slug:
            return ""
        if path_prefix and base_url.endswith(path_prefix):
            return f"{base_url}/{slug}"
        return f"{base_url}{path_prefix}/{slug}"

    def _welcome_text(*, doctor_name: str, clinic_name: str, booking_link: str) -> str:
        template = str(
            getattr(settings, "evolution_welcome_template", "")
            or (
                "Welcome to Dr. {doctor_name} Clinic\n"
                "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है।\n\n"
                "Book your appointment here:\n"
                "अपॉइंटमेंट बुक करने के लिए यहाँ क्लिक करें:\n\n"
                "{booking_link}"
            )
        )
        return template.format(
            doctor_name=doctor_name or "Doctor",
            clinic_name=clinic_name or "Clinic",
            booking_link=booking_link,
        ).strip()

    def _warning_text() -> str:
        return str(
            getattr(settings, "evolution_warning_text", "")
            or (
                "Please use the shared booking link for future appointments.\n"
                "Kindly avoid repeated messages.\n\n"
                "कृपया भविष्य के अपॉइंटमेंट के लिए साझा बुकिंग लिंक का उपयोग करें।\n"
                "बार-बार संदेश न भेजें।"
            )
        ).strip()

    def _verify_secret(request: Request) -> None:
        if not configured_secret:
            return
        presented = str(
            request.headers.get("X-Evolution-Webhook-Secret")
            or request.headers.get("x-evolution-webhook-secret")
            or request.headers.get("apikey")
            or ""
        ).strip()
        if presented != configured_secret:
            raise HTTPException(status_code=403, detail="Invalid Evolution webhook secret")

    async def _handle(request: Request, event_suffix: str = ""):
        started_at = time.perf_counter()
        _verify_secret(request)
        payload = await request.json()
        if isinstance(payload, dict) and event_suffix and not payload.get("event"):
            payload["event"] = event_suffix.replace("-", ".")
        event = _extract_message_event(payload if isinstance(payload, dict) else {})
        if not event:
            return JSONResponse({"ok": True, "status": "ignored"})
        chat_log_id = _chat_log_id(event["remote_jid"])
        log_event(
            chat_log_id,
            "EVOLUTION_WEBHOOK_IN",
            instance=event["instance_name"] or "-",
            remote_jid=event["remote_jid"],
            text=event["text"][:120],
        )
        if (
            not evolution_repository
            or not evolution_policy
            or not evolution_api_client
            or not getattr(evolution_api_client, "enabled", False)
        ):
            logger.warning("Evolution webhook ignored because production dependencies are not configured.")
            log_event(chat_log_id, "EVOLUTION_DISABLED")
            return JSONResponse({"ok": True, "status": "disabled"})
        lookup_started_at = time.perf_counter()
        context = evolution_repository.resolve_doctor_context(
            instance_name=event["instance_name"],
            connected_account="",
        )
        lookup_ms = int((time.perf_counter() - lookup_started_at) * 1000)
        log_event(
            chat_log_id,
            "EVOLUTION_DOCTOR_LOOKUP",
            instance=event["instance_name"] or "-",
            lookup_ms=lookup_ms,
            found=bool(context),
        )
        if not context:
            logger.warning(
                "Evolution webhook ignored because doctor binding was not found instance=%s",
                event["instance_name"] or "-",
            )
            log_event(chat_log_id, "EVOLUTION_UNBOUND", instance=event["instance_name"] or "-")
            return JSONResponse({"ok": True, "status": "unbound"})
        policy_started_at = time.perf_counter()
        count = evolution_policy.register_inbound(
            doctor_id=context.doctor_id,
            patient_identity=event["remote_jid"],
        )
        policy_ms = int((time.perf_counter() - policy_started_at) * 1000)
        log_event(
            chat_log_id,
            "EVOLUTION_POLICY_CHECK",
            doctor_id=context.doctor_id,
            policy_ms=policy_ms,
            count=count,
        )
        reply_text = ""
        status = "silenced"
        if count == 1:
            compose_started_at = time.perf_counter()
            booking_link = _build_booking_link(context.slug)
            reply_text = _welcome_text(
                doctor_name=context.doctor_name,
                clinic_name=context.clinic_name,
                booking_link=booking_link,
            )
            compose_ms = int((time.perf_counter() - compose_started_at) * 1000)
            log_event(
                chat_log_id,
                "EVOLUTION_REPLY_COMPOSED",
                doctor_id=context.doctor_id,
                compose_ms=compose_ms,
                booking_link=booking_link,
            )
            status = "welcome_sent"
        elif count == 2:
            compose_started_at = time.perf_counter()
            reply_text = _warning_text()
            compose_ms = int((time.perf_counter() - compose_started_at) * 1000)
            log_event(
                chat_log_id,
                "EVOLUTION_REPLY_COMPOSED",
                doctor_id=context.doctor_id,
                compose_ms=compose_ms,
                booking_link="",
            )
            status = "warning_sent"
        if reply_text:
            send_started_at = time.perf_counter()
            evolution_api_client.send_text(
                instance_name=event["instance_name"] or context.instance_name,
                remote_jid=event["remote_jid"],
                text=reply_text,
            )
            send_ms = int((time.perf_counter() - send_started_at) * 1000)
            log_event(
                chat_log_id,
                "EVOLUTION_REPLY_SENT",
                doctor_id=context.doctor_id,
                count=count,
                status=status,
                send_ms=send_ms,
                reply=reply_text[:200],
            )
        else:
            log_event(
                chat_log_id,
                "EVOLUTION_REPLY_SKIPPED",
                doctor_id=context.doctor_id,
                count=count,
                status=status,
            )
        total_ms = int((time.perf_counter() - started_at) * 1000)
        log_event(
            chat_log_id,
            "EVOLUTION_WEBHOOK_DONE",
            doctor_id=context.doctor_id,
            count=count,
            status=status,
            total_ms=total_ms,
        )
        logger.info(
            "Evolution inbound handled instance=%s doctor_id=%s patient=%s count=%s status=%s",
            event["instance_name"] or context.instance_name or "-",
            context.doctor_id,
            event["remote_jid"],
            count,
            status,
        )
        return JSONResponse(
            {
                "ok": True,
                "status": status,
                "doctor_id": context.doctor_id,
                "count": count,
            }
        )

    @router.post(webhook_path)
    async def evolution_webhook(request: Request):
        return await _handle(request)

    @router.post(f"{webhook_path}" + "/{event_suffix:path}")
    async def evolution_webhook_by_event(event_suffix: str, request: Request):
        return await _handle(request, event_suffix=event_suffix)

    app.include_router(router)
