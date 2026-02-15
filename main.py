import logging
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

from src.config import load_settings
from src.db_store import BookingRepository
from src.llm.client import LLMClient
from src.ollama_runtime import OllamaStartupError, ensure_ollama_ready
from src.session_store import SessionManager


load_dotenv()
settings = load_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(settings.app_name)

llm_client = LLMClient(
    model=settings.llm_model,
    provider=settings.llm_provider,
    base_url=settings.ollama_base_url,
    timeout_seconds=settings.llm_timeout_seconds,
)
session_manager = SessionManager(
    llm_client=llm_client,
    mixed_response_language=settings.mixed_response_language,
    enable_llm_polish=settings.enable_llm_polish,
    enable_response_polish=settings.enable_response_polish,
    booking_repository=BookingRepository.from_env() if settings.enable_db_booking else None,
    ttl_minutes=settings.session_ttl_minutes,
)
request_validator = RequestValidator(settings.twilio_auth_token)
twilio_client = (
    Client(settings.twilio_account_sid, settings.twilio_auth_token)
    if settings.twilio_account_sid and settings.twilio_auth_token
    else None
)
executor = ThreadPoolExecutor(max_workers=3)
_user_locks: Dict[str, Lock] = {}
_user_locks_guard = Lock()
_seen_message_sids: set[str] = set()
_seen_sid_lock = Lock()

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
async def startup_validation() -> None:
    if settings.llm_provider.lower() != "ollama":
        return
    try:
        ensure_ollama_ready(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            auto_start=settings.ollama_auto_start,
            auto_pull=settings.ollama_auto_pull,
            timeout_seconds=settings.ollama_startup_timeout_seconds,
        )
        LOGGER.info("Ollama ready at %s with model %s", settings.ollama_base_url, settings.llm_model)
    except OllamaStartupError as exc:
        LOGGER.error("Startup validation failed: %s", exc)
        raise RuntimeError(str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.post("/webhook")
async def webhook(request: Request):
    started = time.perf_counter()
    form = await request.form()
    inbound_sid = (form.get("MessageSid") or form.get("SmsMessageSid") or "").strip()
    button_payload = (form.get("ButtonPayload") or "").strip()
    button_text = (form.get("ButtonText") or "").strip()
    body = (
        button_payload
        or button_text
        or (form.get("Body") or "").strip()
    )
    from_number = form.get("From") or "unknown"

    if settings.enable_twilio_signature_validation:
        signature = request.headers.get("X-Twilio-Signature", "")
        valid = request_validator.validate(str(request.url), dict(form), signature)
        if not valid:
            LOGGER.warning("Rejected webhook due to invalid Twilio signature")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    LOGGER.info(
        "Incoming WhatsApp message sid=%s from=%s body=%s button_payload=%s button_text=%s",
        inbound_sid or "-",
        from_number,
        body,
        button_payload or "-",
        button_text or "-",
    )

    if inbound_sid:
        with _seen_sid_lock:
            if inbound_sid in _seen_message_sids:
                LOGGER.info("Duplicate inbound MessageSid ignored sid=%s from=%s", inbound_sid, from_number)
                return PlainTextResponse("", status_code=200)
            _seen_message_sids.add(inbound_sid)

    fsm = session_manager.get_or_create(from_number)
    pre_state = fsm.state

    if settings.twilio_use_rest_responses and twilio_client and settings.twilio_whatsapp_from:
        try:
            future = executor.submit(_process_turn, from_number, body)
            try:
                reply, post_state = future.result(timeout=settings.processing_timeout_seconds)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                LOGGER.info(
                    "Reply generated in %dms sid=%s from=%s state=%s->%s chars=%d",
                    elapsed_ms,
                    inbound_sid or "-",
                    from_number,
                    pre_state,
                    post_state,
                    len(reply),
                )
                _send_whatsapp_response(
                    to_number=from_number,
                    reply_text=reply,
                    fsm_state=post_state,
                    fsm=fsm,
                    inbound_sid=inbound_sid,
                )
                return PlainTextResponse("", status_code=200)
            except TimeoutError:
                safe_msg = _processing_message(fsm.response_language, pre_state)
                _send_plain_rest_message(
                    to_number=from_number,
                    body=safe_msg,
                    inbound_sid=inbound_sid,
                )
                future.add_done_callback(
                    lambda done: _on_background_done(
                        done_future=done,
                        from_number=from_number,
                        inbound_sid=inbound_sid,
                    )
                )
                LOGGER.info(
                    "Deferred slow processing sid=%s from=%s state=%s timeout=%.2fs",
                    inbound_sid or "-",
                    from_number,
                    pre_state,
                    settings.processing_timeout_seconds,
                )
                return PlainTextResponse("", status_code=200)
        except Exception:
            LOGGER.warning("Falling back to TwiML response after REST send failure.")

    reply, post_state = _process_turn(from_number, body)
    reply = reply[: settings.max_message_chars]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    LOGGER.info(
        "Reply generated in %dms sid=%s from=%s state=%s->%s chars=%d",
        elapsed_ms,
        inbound_sid or "-",
        from_number,
        pre_state,
        post_state,
        len(reply),
    )
    twiml = MessagingResponse()
    twiml.message(reply)
    return PlainTextResponse(str(twiml), media_type="application/xml")


@app.post("/twilio/status")
async def twilio_status_callback(request: Request):
    form = await request.form()
    msg_sid = form.get("MessageSid") or form.get("SmsSid") or "-"
    msg_status = form.get("MessageStatus") or form.get("SmsStatus") or "-"
    err_code = form.get("ErrorCode") or "-"
    err_msg = form.get("ErrorMessage") or "-"
    to_number = form.get("To") or "-"
    from_number = form.get("From") or "-"
    LOGGER.info(
        "Twilio status sid=%s status=%s to=%s from=%s error_code=%s error_message=%s",
        msg_sid,
        msg_status,
        to_number,
        from_number,
        err_code,
        err_msg,
    )
    return PlainTextResponse("", status_code=200)


def _process_turn(from_number: str, body: str) -> Tuple[str, str]:
    user_lock = _get_user_lock(from_number)
    with user_lock:
        fsm = session_manager.get_or_create(from_number)
        reply = fsm.handle(body)
        reply = reply[: settings.max_message_chars]
        return reply, fsm.state


def _get_user_lock(user_id: str) -> Lock:
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = Lock()
            _user_locks[user_id] = lock
        return lock


def _on_background_done(done_future, from_number: str, inbound_sid: str) -> None:
    try:
        reply, post_state = done_future.result()
        fsm = session_manager.get_or_create(from_number)
        _send_whatsapp_response(
            to_number=from_number,
            reply_text=reply,
            fsm_state=post_state,
            fsm=fsm,
            inbound_sid=inbound_sid,
        )
        LOGGER.info(
            "Background reply sent sid=%s from=%s state=%s chars=%d",
            inbound_sid or "-",
            from_number,
            post_state,
            len(reply),
        )
    except Exception as exc:
        LOGGER.exception("Background processing failed sid=%s from=%s error=%s", inbound_sid or "-", from_number, exc)


def _processing_message(language: str, state: str) -> str:
    if language == "hi":
        if state == "INIT":
            return "कृपया प्रतीक्षा करें, मैं आपका अनुरोध जाँच रहा हूँ।"
        if state == "CONFIRM":
            return "कृपया प्रतीक्षा करें, मैं आपकी पुष्टि अपडेट कर रहा हूँ।"
        return "कृपया प्रतीक्षा करें, मैं आपकी जानकारी अपडेट कर रहा हूँ।"
    if language == "hinglish":
        if state == "INIT":
            return "Please wait, main aapka request check kar raha hoon."
        if state == "CONFIRM":
            return "Please wait, main aapki confirmation update kar raha hoon."
        return "Please wait, main aapki details update kar raha hoon."
    if state == "INIT":
        return "Please wait, I am checking your request."
    if state == "CONFIRM":
        return "Please wait, I am updating your confirmation."
    return "Please wait, I am updating your details."


def _send_whatsapp_response(to_number: str, reply_text: str, fsm_state: str, fsm, inbound_sid: str = "") -> None:
    if not twilio_client:
        return

    template_sid = _template_for_state(fsm_state)
    content_variables = _content_variables_for_state(fsm_state, fsm)

    try:
        if template_sid:
            kwargs = {
                "from_": settings.twilio_whatsapp_from,
                "to": to_number,
                "content_sid": template_sid,
            }
            if content_variables:
                kwargs["content_variables"] = json.dumps(content_variables, ensure_ascii=False)
            sid = _send_with_retries(kwargs)
            LOGGER.info(
                "Sent template response sid=%s inbound_sid=%s state=%s template_sid=%s",
                sid,
                inbound_sid or "-",
                fsm_state,
                template_sid,
            )
            return

        sid = _send_with_retries(
            {
                "from_": settings.twilio_whatsapp_from,
                "to": to_number,
                "body": reply_text,
            }
        )
        LOGGER.info(
            "Sent plain REST response sid=%s inbound_sid=%s state=%s",
            sid,
            inbound_sid or "-",
            fsm_state,
        )
    except Exception as exc:
        LOGGER.exception("Failed to send WhatsApp response via REST API: %s", exc)
        raise


def _send_plain_rest_message(to_number: str, body: str, inbound_sid: str = "") -> None:
    sid = _send_with_retries(
        {
            "from_": settings.twilio_whatsapp_from,
            "to": to_number,
            "body": body,
        }
    )
    LOGGER.info("Sent safe processing message sid=%s inbound_sid=%s to=%s", sid, inbound_sid or "-", to_number)


def _send_with_retries(kwargs: dict) -> str:
    if not twilio_client:
        return "-"
    if settings.twilio_status_callback_url:
        kwargs = dict(kwargs)
        kwargs["status_callback"] = settings.twilio_status_callback_url
    last_exc = None
    total_attempts = max(1, settings.twilio_send_retries + 1)
    for attempt in range(1, total_attempts + 1):
        try:
            message = twilio_client.messages.create(**kwargs)
            return message.sid
        except Exception as exc:
            last_exc = exc
            LOGGER.warning("Twilio send attempt failed attempt=%d/%d error=%s", attempt, total_attempts, exc)
            if attempt == total_attempts:
                raise
            time.sleep(0.8 * attempt)
    if last_exc:
        raise last_exc
    return "-"


def _template_for_state(state: str) -> str:
    mapping = {
        "ASK_PATIENT_TYPE": settings.twilio_template_patient_type_sid,
        "ASK_GENDER": settings.twilio_template_gender_sid,
        "ASK_PHONE": settings.twilio_template_phone_choice_sid,
        "ASK_CLINIC": settings.twilio_template_clinic_sid,
        "ASK_REASON": settings.twilio_template_reason_sid,
        "ASK_DATE": settings.twilio_template_date_sid,
        "ASK_TIME": settings.twilio_template_time_sid,
    }
    return mapping.get(state, "")


def _content_variables_for_state(state: str, fsm) -> dict:
    if state == "ASK_DATE":
        d1, d2, d3 = fsm._date_options()
        return {"1": d1, "2": d2, "3": d3}
    if state == "ASK_TIME":
        slots = fsm._suggested_slots()
        return {"1": slots[0], "2": slots[1], "3": slots[2]}
    return {}
