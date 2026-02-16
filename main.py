import logging
import json
import os
import time
import threading
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

from src.api.admin_router import create_admin_router
from src.config import load_settings
from src.db_store import auth_repository_from_env, conversation_repository_from_env, repositories_from_env
from src.llm.client import LLMClient
from src.ollama_runtime import OllamaStartupError, ensure_ollama_ready
from src.runtime import PersistentMessageSidStore, TurnQueueProcessor, TurnTask
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
booking_repository, scheduling_repository = repositories_from_env()
conversation_repository = conversation_repository_from_env()
auth_repository = auth_repository_from_env()

session_manager = SessionManager(
    llm_client=llm_client,
    mixed_response_language=settings.mixed_response_language,
    enable_llm_polish=settings.enable_llm_polish,
    booking_repository=booking_repository if settings.enable_db_booking else None,
    scheduling_repository=scheduling_repository if settings.enable_db_booking else None,
    conversation_repository=conversation_repository if settings.enable_db_booking else None,
    bot_whatsapp_number=settings.twilio_whatsapp_from,
    ttl_minutes=settings.session_ttl_minutes,
)
request_validator = RequestValidator(settings.twilio_auth_token)
twilio_client = (
    Client(settings.twilio_account_sid, settings.twilio_auth_token)
    if settings.twilio_account_sid and settings.twilio_auth_token
    else None
)
_user_locks: Dict[str, Lock] = {}
_user_locks_guard = Lock()

SID_STORE_PATH = os.path.join("data", "seen_message_sids.jsonl")
sid_store = PersistentMessageSidStore(path=SID_STORE_PATH, max_entries=50000)

turn_processor = TurnQueueProcessor(
    worker_count=max(1, settings.queue_worker_count),
    max_queue_size=max(1, settings.queue_max_size),
    process_fn=lambda from_number, body: _process_turn(from_number, body),
    send_fn=lambda to_number, reply, post_state, inbound_sid: _send_whatsapp_response(
        to_number=to_number,
        reply_text=reply,
        fsm_state=post_state,
        fsm=session_manager.get_or_create(to_number),
        inbound_sid=inbound_sid,
    ),
    retry_attempts=max(0, settings.queue_retry_attempts),
)

app = FastAPI(title=settings.app_name)
app.include_router(
    create_admin_router(
        booking_repository,
        scheduling_repository,
        auth_repository=auth_repository,
        admin_api_key=settings.admin_api_key,
        rate_limit_per_minute=settings.admin_api_rate_limit_per_minute,
        token_ttl_minutes=settings.admin_auth_token_ttl_minutes,
    )
)


@app.on_event("startup")
async def startup_validation() -> None:
    turn_processor.start()
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


@app.on_event("shutdown")
async def shutdown_workers() -> None:
    turn_processor.stop()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/queue")
async def health_queue() -> dict:
    queue_metrics = turn_processor.snapshot()
    dedup_db_size = 0
    if conversation_repository:
        try:
            dedup_db_size = conversation_repository.dedup_size()
        except Exception:
            dedup_db_size = 0
    return {
        "status": "ok",
        "queue": queue_metrics,
        "dedup": {
            "file_size": sid_store.size(),
            "db_size": dedup_db_size,
        },
        "config": {
            "queue_worker_count": settings.queue_worker_count,
            "queue_max_size": settings.queue_max_size,
            "queue_retry_attempts": settings.queue_retry_attempts,
            "queue_busy_threshold": settings.queue_busy_threshold,
            "queue_overflow_requeue_attempts": settings.queue_overflow_requeue_attempts,
            "queue_overflow_requeue_backoff_seconds": settings.queue_overflow_requeue_backoff_seconds,
        },
    }


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
        duplicate = False
        if conversation_repository:
            try:
                duplicate = conversation_repository.seen_or_add_message_sid(
                    message_sid=inbound_sid,
                    user_id=from_number,
                    body=body,
                )
            except Exception:
                duplicate = sid_store.seen_or_add(inbound_sid)
        else:
            duplicate = sid_store.seen_or_add(inbound_sid)
        if duplicate:
            LOGGER.info("Duplicate inbound MessageSid ignored sid=%s from=%s", inbound_sid, from_number)
            return PlainTextResponse("", status_code=200)

    fsm = session_manager.get_or_create(from_number)
    pre_state = fsm.state

    if settings.twilio_use_rest_responses and twilio_client and settings.twilio_whatsapp_from:
        try:
            task = TurnTask(
                from_number=from_number,
                body=body,
                inbound_sid=inbound_sid,
                pre_state=pre_state,
            )
            enqueued = turn_processor.submit(task)
            if not enqueued:
                busy_msg = _busy_message(fsm.response_language, pre_state)
                _send_plain_rest_message(to_number=from_number, body=busy_msg, inbound_sid=inbound_sid)
                _schedule_overflow_requeue(task)
                LOGGER.warning(
                    "Queue full. Busy message sent sid=%s from=%s backlog=%d",
                    inbound_sid or "-",
                    from_number,
                    turn_processor.backlog_size(),
                )
                return PlainTextResponse("", status_code=200)

            # If queue is already backlogged, proactively send processing notice.
            if turn_processor.backlog_size() >= max(1, settings.queue_busy_threshold):
                safe_msg = _processing_message(fsm.response_language, pre_state)
                _send_plain_rest_message(to_number=from_number, body=safe_msg, inbound_sid=inbound_sid)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            LOGGER.info(
                "Queued inbound sid=%s from=%s state=%s backlog=%d ack_ms=%d",
                inbound_sid or "-",
                from_number,
                pre_state,
                turn_processor.backlog_size(),
                elapsed_ms,
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
        session_manager.save(from_number)
        return reply, fsm.state


def _get_user_lock(user_id: str) -> Lock:
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = Lock()
            _user_locks[user_id] = lock
        return lock


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


def _busy_message(language: str, state: str) -> str:
    if language == "hi":
        return "इस समय सभी सहायक व्यस्त हैं। कृपया कुछ देर बाद पुनः प्रयास करें।"
    if language == "hinglish":
        return "Is waqt sab assistants busy hain. Please thodi der baad try kariye."
    return "All assistants are busy right now. Please try again shortly."


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


def _schedule_overflow_requeue(task: TurnTask) -> None:
    max_attempts = max(1, settings.queue_overflow_requeue_attempts)
    base_backoff = max(0.2, settings.queue_overflow_requeue_backoff_seconds)

    def _runner() -> None:
        for attempt in range(1, max_attempts + 1):
            if turn_processor.submit(task):
                LOGGER.info(
                    "Overflow task requeued sid=%s from=%s attempt=%d/%d",
                    task.inbound_sid or "-",
                    task.from_number,
                    attempt,
                    max_attempts,
                )
                return
            sleep_for = min(8.0, base_backoff * attempt)
            time.sleep(sleep_for)
        LOGGER.error(
            "Overflow task dropped after requeue attempts sid=%s from=%s attempts=%d",
            task.inbound_sid or "-",
            task.from_number,
            max_attempts,
        )

    threading.Thread(
        target=_runner,
        name=f"overflow-requeue-{task.inbound_sid or 'na'}",
        daemon=True,
    ).start()


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
        dates = fsm._date_options()
        if not dates:
            return {}
        d1, d2, d3 = dates
        return {"1": d1, "2": d2, "3": d3}
    if state == "ASK_TIME":
        slots = fsm._suggested_slots()
        if len(slots) < 3:
            return {}
        return {"1": slots[0], "2": slots[1], "3": slots[2]}
    return {}
