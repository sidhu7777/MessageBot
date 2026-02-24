import logging
import json
import os
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib import error as urlerror
from urllib import request as urlrequest
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

from src.automation import AutomationScheduler
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
_telegram_bot_username_runtime = ""

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
    bot_whatsapp_number="",
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
_overflow_poll_stop = threading.Event()
_overflow_poll_thread: threading.Thread | None = None
_overflow_worker_id = f"overflow-{uuid.uuid4().hex[:10]}"
_overflow_turn_map: dict[str, int] = {}
_overflow_turn_map_lock = Lock()
_process_executor = ThreadPoolExecutor(max_workers=max(4, settings.queue_worker_count * 2))

turn_processor = TurnQueueProcessor(
    worker_count=max(1, settings.queue_worker_count),
    max_queue_size=max(1, settings.queue_max_size),
    process_fn=lambda from_number, body: _process_turn_with_timeout(from_number, body),
    send_fn=lambda to_number, reply, post_state, inbound_sid: _send_channel_response(
        to_number=to_number,
        reply_text=reply,
        fsm_state=post_state,
        fsm=session_manager.get_or_create(to_number),
        inbound_sid=inbound_sid,
    ),
    retry_attempts=max(0, settings.queue_retry_attempts),
    timeout_fn=lambda task, exc: _handle_turn_timeout(task, exc),
    on_success=lambda task: _on_turn_success(task),
    on_failure=lambda task, exc, will_retry, backoff: _on_turn_failure(task, exc, will_retry, backoff),
)
automation_scheduler = AutomationScheduler(
    booking_repository=booking_repository if settings.enable_db_booking else None,
    send_message_fn=lambda to_number, body: _send_plain_channel_message(to_number=to_number, body=body),
    send_document_fn=lambda to_number, file_path, caption: _send_plain_channel_document(
        to_number=to_number,
        file_path=file_path,
        caption=caption,
    ),
    source_whatsapp_number=settings.twilio_whatsapp_from,
    enabled=settings.automation_enabled,
    doctor_reminder_enabled=settings.doctor_reminder_enabled,
    doctor_reminder_interval_seconds=settings.doctor_reminder_interval_seconds,
    doctor_reminder_lead_minutes=settings.doctor_reminder_lead_minutes,
    doctor_reminder_window_seconds=settings.doctor_reminder_window_seconds,
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
    global _telegram_bot_username_runtime, _overflow_poll_thread
    if settings.enable_db_booking and booking_repository:
        try:
            booking_repository.ensure_notification_schema()
        except Exception as exc:
            LOGGER.warning("Notification schema ensure failed: %s", exc)
    if conversation_repository:
        try:
            conversation_repository.ensure_schema()
        except Exception as exc:
            LOGGER.warning("Conversation schema ensure failed: %s", exc)
    turn_processor.start()
    if conversation_repository and (_overflow_poll_thread is None or not _overflow_poll_thread.is_alive()):
        _overflow_poll_stop.clear()
        _overflow_poll_thread = threading.Thread(
            target=_overflow_turn_poll_loop,
            name="overflow-turn-poller",
            daemon=True,
        )
        _overflow_poll_thread.start()
    automation_scheduler.start()
    resolved_username = _resolve_telegram_bot_username()
    if resolved_username:
        _telegram_bot_username_runtime = resolved_username
        LOGGER.info("Telegram bot username resolved dynamically: @%s", resolved_username)
    elif settings.telegram_bot_username:
        _telegram_bot_username_runtime = settings.telegram_bot_username
        LOGGER.warning(
            "Telegram bot username dynamic resolution failed; using TELEGRAM_BOT_USERNAME fallback: @%s",
            settings.telegram_bot_username,
        )
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
    _overflow_poll_stop.set()
    if _overflow_poll_thread and _overflow_poll_thread.is_alive():
        _overflow_poll_thread.join(timeout=2.0)
    automation_scheduler.stop()
    turn_processor.stop()
    _process_executor.shutdown(wait=False, cancel_futures=True)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/queue")
async def health_queue() -> dict:
    queue_metrics = turn_processor.snapshot()
    dedup_db_size = 0
    overflow_stats = {"queued": 0, "processing": 0, "dead": 0}
    notification_stats = {"queued": 0, "dead": 0}
    if conversation_repository:
        try:
            dedup_db_size = conversation_repository.dedup_size()
            overflow_stats = conversation_repository.overflow_queue_stats()
        except Exception:
            dedup_db_size = 0
    if booking_repository:
        try:
            notification_stats = booking_repository.notification_queue_stats()
        except Exception:
            notification_stats = {"queued": 0, "dead": 0}
    return {
        "status": "ok",
        "queue": queue_metrics,
        "overflow_queue": overflow_stats,
        "notification_queue": notification_stats,
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
            "automation_enabled": settings.automation_enabled,
            "doctor_reminder_enabled": settings.doctor_reminder_enabled,
            "doctor_reminder_interval_seconds": settings.doctor_reminder_interval_seconds,
            "doctor_reminder_lead_minutes": settings.doctor_reminder_lead_minutes,
            "doctor_reminder_window_seconds": settings.doctor_reminder_window_seconds,
        },
        "automation": automation_scheduler.snapshot(),
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
    to_number = (form.get("To") or settings.twilio_whatsapp_from or "").strip()

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
    if to_number:
        fsm.bot_whatsapp_number = to_number
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
                _send_plain_channel_message(to_number=from_number, body=busy_msg, inbound_sid=inbound_sid)
                _enqueue_overflow_turn(task)
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
                _send_plain_channel_message(to_number=from_number, body=safe_msg, inbound_sid=inbound_sid)

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
    if not (reply or "").strip():
        LOGGER.info("Direct webhook reply suppressed sid=%s from=%s (empty/silent response).", inbound_sid or "-", from_number)
        return PlainTextResponse("", status_code=200)
    twiml = MessagingResponse()
    twiml.message(reply)
    return PlainTextResponse(str(twiml), media_type="application/xml")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if settings.telegram_webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != settings.telegram_webhook_secret:
            LOGGER.warning("Rejected Telegram webhook due to invalid secret token")
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    started = time.perf_counter()
    payload = await request.json()
    message = payload.get("message") or payload.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    from_user = message.get("from") or {}
    chat = message.get("chat") or {}
    telegram_user_id = str(from_user.get("id") or chat.get("id") or "").strip()
    inbound_sid = str(message.get("message_id") or "").strip()
    if not telegram_user_id:
        return PlainTextResponse("", status_code=200)
    from_number = f"telegram:{telegram_user_id}"

    LOGGER.info(
        "Incoming Telegram message sid=%s from=%s body=%s",
        inbound_sid or "-",
        from_number,
        text,
    )

    if inbound_sid:
        dedup_sid = f"TG{telegram_user_id}:{inbound_sid}"
        # Keep Telegram webhook ack path fast to avoid Telegram read timeouts.
        # Use local/file dedup here; DB dedup can block webhook response under load.
        duplicate = sid_store.seen_or_add(dedup_sid)
        if duplicate:
            LOGGER.info("Duplicate inbound Telegram message ignored sid=%s from=%s", dedup_sid, from_number)
            return PlainTextResponse("", status_code=200)

    fsm = session_manager.get_or_create(from_number)
    if _telegram_bot_username_runtime:
        fsm.bot_whatsapp_number = f"telegram_username:{_telegram_bot_username_runtime}"
    pre_state = fsm.state
    try:
        task = TurnTask(
            from_number=from_number,
            body=text,
            inbound_sid=inbound_sid,
            pre_state=pre_state,
        )
        enqueued = turn_processor.submit(task)
        if not enqueued:
            busy_msg = _busy_message(fsm.response_language, pre_state)
            _send_plain_channel_message(to_number=from_number, body=busy_msg, inbound_sid=inbound_sid)
            _enqueue_overflow_turn(task)
            LOGGER.warning(
                "Queue full. Busy message sent sid=%s from=%s backlog=%d",
                inbound_sid or "-",
                from_number,
                turn_processor.backlog_size(),
            )
            return PlainTextResponse("", status_code=200)

        if turn_processor.backlog_size() >= max(1, settings.queue_busy_threshold):
            safe_msg = _processing_message(fsm.response_language, pre_state)
            _send_plain_channel_message(to_number=from_number, body=safe_msg, inbound_sid=inbound_sid)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        LOGGER.info(
            "Queued inbound Telegram sid=%s from=%s state=%s backlog=%d ack_ms=%d",
            inbound_sid or "-",
            from_number,
            pre_state,
            turn_processor.backlog_size(),
            elapsed_ms,
        )
        return PlainTextResponse("", status_code=200)
    except Exception:
        LOGGER.warning("Falling back to direct Telegram response after queue failure.")
        reply, post_state = _process_turn(from_number, text)
        reply = reply[: settings.max_message_chars]
        if (reply or "").strip():
            _send_plain_channel_message(to_number=from_number, body=reply, inbound_sid=inbound_sid)
        else:
            LOGGER.info("Telegram fallback reply suppressed sid=%s from=%s (empty/silent response).", inbound_sid or "-", from_number)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        LOGGER.info(
            "Telegram fallback reply generated in %dms sid=%s from=%s state=%s->%s chars=%d",
            elapsed_ms,
            inbound_sid or "-",
            from_number,
            pre_state,
            post_state,
            len(reply),
        )
        return PlainTextResponse("", status_code=200)


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
    if booking_repository:
        try:
            booking_repository.upsert_delivery_status(
                provider="twilio",
                provider_message_sid=str(msg_sid),
                channel="whatsapp",
                message_status=str(msg_status),
                to_number=str(to_number),
                from_number=str(from_number),
                error_code=str(err_code),
                error_message=str(err_msg),
                payload_json=json.dumps(dict(form), ensure_ascii=False),
            )
        except Exception as exc:
            LOGGER.warning("Failed to persist Twilio callback sid=%s error=%s", msg_sid, exc)
    return PlainTextResponse("", status_code=200)


def _process_turn(from_number: str, body: str) -> Tuple[str, str]:
    user_lock = _get_user_lock(from_number)
    with user_lock:
        fsm = session_manager.get_or_create(from_number)
        reply = fsm.handle(body)
        reply = reply[: settings.max_message_chars]
        session_manager.save(from_number)
        return reply, fsm.state


def _process_turn_with_timeout(from_number: str, body: str) -> Tuple[str, str]:
    timeout_seconds = max(0.5, float(settings.processing_timeout_seconds))
    future = _process_executor.submit(_process_turn, from_number, body)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"Turn processing timeout after {timeout_seconds:.1f}s") from exc


def _timeout_message(language: str, state: str) -> str:
    if language == "hi":
        return "Abhi processing mein delay ho raha hai. Kripaya thoda intezar karein, hum jaldi reply karenge."
    if language == "hinglish":
        return "Processing mein delay ho raha hai. Please wait, hum jaldi response denge."
    return "We are facing a delay while processing your request. Please wait, we will respond shortly."


def _handle_turn_timeout(task: TurnTask, exc: Exception) -> None:
    try:
        fsm = session_manager.get_or_create(task.from_number)
        timeout_msg = _timeout_message(fsm.response_language, task.pre_state)
        _send_plain_channel_message(
            to_number=task.from_number,
            body=timeout_msg,
            inbound_sid=task.inbound_sid,
        )
    except Exception:
        LOGGER.exception("Failed timeout-safe message sid=%s error=%s", task.inbound_sid or "-", exc)


def _track_overflow_task(task: TurnTask, queue_id: int) -> None:
    sid = (task.inbound_sid or "").strip()
    if not sid:
        return
    with _overflow_turn_map_lock:
        _overflow_turn_map[sid] = int(queue_id)


def _pop_overflow_queue_id(task: TurnTask) -> int:
    sid = (task.inbound_sid or "").strip()
    if not sid:
        return 0
    with _overflow_turn_map_lock:
        return int(_overflow_turn_map.pop(sid, 0) or 0)


def _get_overflow_queue_id(task: TurnTask) -> int:
    sid = (task.inbound_sid or "").strip()
    if not sid:
        return 0
    with _overflow_turn_map_lock:
        return int(_overflow_turn_map.get(sid, 0) or 0)


def _on_turn_success(task: TurnTask) -> None:
    queue_id = _pop_overflow_queue_id(task)
    if queue_id and conversation_repository:
        try:
            conversation_repository.mark_overflow_turn_done(queue_id=queue_id)
        except Exception:
            LOGGER.exception("Failed to mark overflow turn done sid=%s queue_id=%s", task.inbound_sid or "-", queue_id)


def _on_turn_failure(task: TurnTask, exc: Exception, will_retry: bool, backoff_seconds: float) -> None:
    queue_id = _get_overflow_queue_id(task)
    if not queue_id or not conversation_repository:
        return
    try:
        if will_retry:
            # Keep row as PROCESSING while in-memory retry is in progress.
            return
        # Final failure: persist dead and clear in-memory map.
        _pop_overflow_queue_id(task)
        conversation_repository.mark_overflow_turn_retry(
            queue_id=queue_id,
            error_text=str(exc),
            backoff_seconds=1,
            max_attempts=1,
        )
    except Exception:
        LOGGER.exception("Failed to update overflow retry state sid=%s queue_id=%s", task.inbound_sid or "-", queue_id)


def _enqueue_overflow_turn(task: TurnTask) -> None:
    if not conversation_repository:
        _schedule_overflow_requeue(task)
        return
    try:
        conversation_repository.enqueue_overflow_turn(
            inbound_sid=task.inbound_sid,
            from_number=task.from_number,
            body=task.body,
            pre_state=task.pre_state,
        )
    except Exception as exc:
        LOGGER.warning("Overflow DB enqueue failed sid=%s error=%s; falling back to in-memory requeue.", task.inbound_sid or "-", exc)
        _schedule_overflow_requeue(task)


def _overflow_turn_poll_loop() -> None:
    if not conversation_repository:
        return
    claim_size = max(1, settings.queue_worker_count)
    while not _overflow_poll_stop.is_set():
        try:
            rows = conversation_repository.claim_overflow_turns(limit=claim_size, worker_id=_overflow_worker_id)
            if not rows:
                _overflow_poll_stop.wait(0.8)
                continue
            for row in rows:
                task = TurnTask(
                    from_number=row.from_number,
                    body=row.body,
                    inbound_sid=row.inbound_sid,
                    pre_state=row.pre_state,
                    attempt=max(0, int(row.attempt_count)),
                )
                if turn_processor.submit(task):
                    _track_overflow_task(task, row.queue_id)
                    continue
                conversation_repository.release_overflow_turn(
                    queue_id=row.queue_id,
                    reason="Runtime queue still full",
                    backoff_seconds=max(1, int(settings.queue_overflow_requeue_backoff_seconds)),
                )
        except Exception as exc:
            LOGGER.warning("Overflow queue poll failed error=%s", exc)
            _overflow_poll_stop.wait(1.0)


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


def _send_channel_response(to_number: str, reply_text: str, fsm_state: str, fsm, inbound_sid: str = "") -> None:
    if not (reply_text or "").strip():
        LOGGER.info("Reply suppressed for sid=%s to=%s (empty/silent response).", inbound_sid or "-", to_number)
        return
    if (to_number or "").strip().lower().startswith("telegram:"):
        _send_plain_channel_message(to_number=to_number, body=reply_text, inbound_sid=inbound_sid)
        return
    _send_whatsapp_response(to_number=to_number, reply_text=reply_text, fsm_state=fsm_state, fsm=fsm, inbound_sid=inbound_sid)


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


def _send_plain_channel_message(to_number: str, body: str, inbound_sid: str = "") -> str:
    if (to_number or "").strip().lower().startswith("telegram:"):
        _send_telegram_message(to_number=to_number, body=body, inbound_sid=inbound_sid)
        return "telegram"
    sid = _send_with_retries(
        {
            "from_": settings.twilio_whatsapp_from,
            "to": to_number,
            "body": body,
        }
    )
    LOGGER.info("Sent safe processing message sid=%s inbound_sid=%s to=%s", sid, inbound_sid or "-", to_number)
    return sid

def _send_plain_channel_document(to_number: str, file_path: str, caption: str = "", inbound_sid: str = "") -> None:
    if (to_number or "").strip().lower().startswith("telegram:"):
        _send_telegram_document(
            to_number=to_number,
            file_path=file_path,
            caption=caption,
            inbound_sid=inbound_sid,
        )
        return
    # Twilio WhatsApp media requires a publicly reachable media URL.
    # For local scheduler exports, keep a safe fallback text notification.
    msg = caption.strip() if caption else "Doctor reminder report generated."
    msg = f"{msg}\nReport file: {os.path.basename(file_path)}"
    _send_plain_channel_message(to_number=to_number, body=msg, inbound_sid=inbound_sid)


def _send_telegram_message(to_number: str, body: str, inbound_sid: str = "") -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    chat_id = _telegram_chat_id_from_user(to_number)
    if not chat_id:
        raise RuntimeError(f"Invalid Telegram destination: {to_number}")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": body}).encode("utf-8")
    req = urlrequest.Request(
        url=url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            LOGGER.info(
                "Sent Telegram message inbound_sid=%s to=%s response=%s",
                inbound_sid or "-",
                to_number,
                raw[:200],
            )
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        LOGGER.error("Telegram send failed status=%s body=%s", exc.code, detail[:400])
        raise
    except Exception as exc:
        LOGGER.error("Telegram send failed error=%s", exc)
        raise

def _send_telegram_document(to_number: str, file_path: str, caption: str = "", inbound_sid: str = "") -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    chat_id = _telegram_chat_id_from_user(to_number)
    if not chat_id:
        raise RuntimeError(f"Invalid Telegram destination: {to_number}")
    if not os.path.exists(file_path):
        raise RuntimeError(f"Report file not found: {file_path}")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as handle:
        file_bytes = handle.read()

    parts = bytearray()

    def _append_field(name: str, value: str) -> None:
        parts.extend(f"--{boundary}\r\n".encode("utf-8"))
        parts.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.extend((value or "").encode("utf-8"))
        parts.extend(b"\r\n")

    _append_field("chat_id", chat_id)
    if caption:
        _append_field("caption", caption)

    parts.extend(f"--{boundary}\r\n".encode("utf-8"))
    parts.extend(
        (
            f'Content-Disposition: form-data; name="document"; filename="{file_name}"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode("utf-8")
    )
    parts.extend(file_bytes)
    parts.extend(b"\r\n")
    parts.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urlrequest.Request(
        url=url,
        data=bytes(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            LOGGER.info(
                "Sent Telegram document inbound_sid=%s to=%s file=%s response=%s",
                inbound_sid or "-",
                to_number,
                file_name,
                raw[:200],
            )
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        LOGGER.error("Telegram document send failed status=%s body=%s", exc.code, detail[:400])
        raise
    except Exception as exc:
        LOGGER.error("Telegram document send failed error=%s", exc)
        raise


def _telegram_chat_id_from_user(user_id: str) -> str:
    raw = (user_id or "").strip()
    if raw.startswith("telegram:"):
        return raw[len("telegram:") :]
    return raw


def _resolve_telegram_bot_username() -> str:
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return ""
    url = f"https://api.telegram.org/bot{token}/getMe"
    req = urlrequest.Request(url=url, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(payload, dict) or not payload.get("ok"):
            return ""
        result = payload.get("result") or {}
        username = str(result.get("username") or "").strip().lstrip("@")
        return username
    except Exception:
        return ""


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
