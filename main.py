import logging
import json
import os
import time
import threading
import uuid
from contextlib import asynccontextmanager
from urllib import error as urlerror
from urllib import request as urlrequest
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from dotenv import load_dotenv

from src.automation import AutomationScheduler
from src.config import load_settings
from src.db_store import conversation_repository_from_env, repositories_from_env
from src.llm.client import LLMClient
from src.ollama_runtime import OllamaStartupError, ensure_ollama_ready
from src.runtime import PersistentMessageSidStore, TurnQueueProcessor, TurnTask
from src.runtime.user_turn_buffer import UserTurnBuffer
from src.runtime.user_processing_guard import UserProcessingGuard, build_redis_client_from_env
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

session_manager = SessionManager(
    llm_client=llm_client,
    mixed_response_language=settings.mixed_response_language,
    enable_llm_polish=settings.enable_llm_polish,
    booking_repository=booking_repository if settings.enable_db_booking else None,
    scheduling_repository=scheduling_repository if settings.enable_db_booking else None,
    conversation_repository=conversation_repository if settings.enable_db_booking else None,
    redis_key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
    bot_whatsapp_number="",
    ttl_minutes=settings.session_ttl_minutes,
)
request_validator = RequestValidator(settings.twilio_auth_token)
twilio_client = (
    Client(settings.twilio_account_sid, settings.twilio_auth_token)
    if settings.twilio_account_sid and settings.twilio_auth_token
    else None
)
# Use striped locks (fixed-size pool) to avoid unbounded per-user lock growth.
_user_lock_stripes_count = max(64, int(os.getenv("USER_LOCK_STRIPES", "4096")))
_user_lock_stripes = [Lock() for _ in range(_user_lock_stripes_count)]

SID_STORE_PATH = os.path.join("data", "seen_message_sids.jsonl")
sid_store = PersistentMessageSidStore(path=SID_STORE_PATH, max_entries=50000)
_overflow_poll_stop = threading.Event()
_overflow_poll_thread: threading.Thread | None = None
_overflow_worker_id = f"overflow-{uuid.uuid4().hex[:10]}"
_cache_inv_stop = threading.Event()
_cache_inv_thread: threading.Thread | None = None
_cache_inv_worker_id = f"dcache-{uuid.uuid4().hex[:10]}"
_overflow_turn_map: dict[str, int] = {}
_overflow_turn_map_lock = Lock()
_user_bot_identity: Dict[str, str] = {}
_user_bot_identity_lock = Lock()
_user_turn_generation: Dict[str, int] = {}
_user_turn_generation_lock = Lock()
# Maximum number of per-user entries kept in the identity/generation dicts.
# Protects against unbounded memory growth with many unique users.
_PER_USER_DICT_MAX = max(5000, int(os.getenv("PER_USER_DICT_MAX", "20000")))


def _evict_dict_if_needed(d: dict, lock: Lock) -> None:
    """Drop the oldest half of entries once the dict exceeds _PER_USER_DICT_MAX."""
    with lock:
        if len(d) <= _PER_USER_DICT_MAX:
            return
        keep = _PER_USER_DICT_MAX // 2
        keys_to_delete = list(d.keys())[:-keep] if keep else list(d.keys())
        for k in keys_to_delete:
            d.pop(k, None)
_ollama_max_concurrency = max(1, int(os.getenv("OLLAMA_MAX_CONCURRENCY", "1")))
_ollama_semaphore = threading.Semaphore(_ollama_max_concurrency)
_redis_client = build_redis_client_from_env()
session_manager.redis_client = _redis_client
if scheduling_repository:
    scheduling_repository.set_redis_client(_redis_client)
    scheduling_repository.set_cache_config(
        ttl_seconds=int(os.getenv("REDIS_DOCTOR_CACHE_TTL_SECONDS", "3600")),
        key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
    )
_user_processing_guard = UserProcessingGuard(
    redis_client=_redis_client,
    lock_ttl_seconds=int(os.getenv("REDIS_PROCESSING_TTL_SECONDS", "45")),
    busy_ttl_seconds=int(os.getenv("REDIS_BUSY_HINT_TTL_SECONDS", "8")),
    key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
)
_user_turn_buffer = UserTurnBuffer(
    max_per_user=int(os.getenv("PER_USER_QUEUE_MAX", "5")),
    collapse_window_seconds=float(os.getenv("PER_USER_COALESCE_WINDOW_SECONDS", "6")),
)


def _set_user_bot_identity(user_id: str, identity: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    with _user_bot_identity_lock:
        if identity:
            _user_bot_identity[uid] = identity
        else:
            _user_bot_identity.pop(uid, None)
    _evict_dict_if_needed(_user_bot_identity, _user_bot_identity_lock)


def _get_user_bot_identity(user_id: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return ""
    with _user_bot_identity_lock:
        return str(_user_bot_identity.get(uid) or "")


def _next_user_turn_generation(user_id: str) -> int:
    uid = (user_id or "").strip()
    if not uid:
        return 0
    with _user_turn_generation_lock:
        next_value = int(_user_turn_generation.get(uid, 0)) + 1
        _user_turn_generation[uid] = next_value
    _evict_dict_if_needed(_user_turn_generation, _user_turn_generation_lock)
    return next_value


def _is_user_turn_generation_current(user_id: str, generation: int) -> bool:
    uid = (user_id or "").strip()
    if not uid:
        return True
    with _user_turn_generation_lock:
        return int(_user_turn_generation.get(uid, 0)) == int(generation)

turn_processor = TurnQueueProcessor(
    worker_count=max(1, settings.queue_worker_count),
    max_queue_size=max(1, settings.queue_max_size),
    process_fn=lambda from_number, body: _process_turn(from_number, body),
    send_fn=lambda to_number, reply, post_state, inbound_sid, fsm=None: _send_channel_response(
        to_number=to_number,
        reply_text=reply,
        fsm_state=post_state,
        fsm=fsm or session_manager.get_or_create(to_number),
        inbound_sid=inbound_sid,
    ),
    retry_attempts=max(0, settings.queue_retry_attempts),
    processing_timeout_seconds=max(0.0, float(settings.processing_timeout_seconds)),
    timeout_fn=lambda task, exc: _handle_turn_timeout(task, exc),
    on_success=lambda task: _on_turn_success(task),
    on_failure=lambda task, exc, will_retry, backoff: _on_turn_failure(task, exc, will_retry, backoff),
)
_doctor_reminder_lead_minutes_list: Optional[list] = None
_raw_lead_list = os.getenv("DOCTOR_REMINDER_LEAD_MINUTES_LIST", "").strip()
if _raw_lead_list:
    try:
        _doctor_reminder_lead_minutes_list = [
            max(1, int(x.strip())) for x in _raw_lead_list.split(",") if x.strip()
        ]
    except Exception:
        LOGGER.warning(
            "Invalid DOCTOR_REMINDER_LEAD_MINUTES_LIST=%r — using default.", _raw_lead_list
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
    doctor_reminder_lead_minutes_list=_doctor_reminder_lead_minutes_list,
)

@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    await startup_validation()
    try:
        yield
    finally:
        await shutdown_workers()


app = FastAPI(title=settings.app_name, lifespan=_app_lifespan)


async def startup_validation() -> None:
    global _telegram_bot_username_runtime, _overflow_poll_thread, _cache_inv_thread
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
    if scheduling_repository:
        try:
            scheduling_repository.ensure_cache_invalidation_schema()
        except Exception as exc:
            LOGGER.warning("Doctor cache invalidation schema ensure failed: %s", exc)
        if _cache_inv_thread is None or not _cache_inv_thread.is_alive():
            _cache_inv_stop.clear()
            _cache_inv_thread = threading.Thread(
                target=_doctor_cache_invalidation_loop,
                name="doctor-cache-invalidation-poller",
                daemon=True,
            )
            _cache_inv_thread.start()
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


async def shutdown_workers() -> None:
    _cache_inv_stop.set()
    if _cache_inv_thread and _cache_inv_thread.is_alive():
        _cache_inv_thread.join(timeout=2.0)
    _overflow_poll_stop.set()
    if _overflow_poll_thread and _overflow_poll_thread.is_alive():
        _overflow_poll_thread.join(timeout=2.0)
    automation_scheduler.stop()
    turn_processor.stop()


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
        # Keep webhook path ultra-light: fast local dedup only.
        duplicate = sid_store.seen_or_add(inbound_sid)
        if duplicate:
            LOGGER.info("Duplicate inbound MessageSid ignored sid=%s from=%s", inbound_sid, from_number)
            return PlainTextResponse("", status_code=200)
    _set_user_bot_identity(from_number, to_number)
    try:
        pre_state = session_manager.get_or_create(from_number).state
    except Exception:
        pre_state = "INIT"

    if settings.twilio_use_rest_responses and twilio_client and settings.twilio_whatsapp_from:
        acquired = False
        try:
            acquired = _user_processing_guard.acquire(from_number)
            if not acquired:
                buffered = _user_turn_buffer.push(
                    TurnTask(
                        from_number=from_number,
                        body=body,
                        inbound_sid=inbound_sid,
                        pre_state=pre_state,
                    )
                )
                LOGGER.info(
                    "Buffered inbound while processing sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
                    inbound_sid or "-",
                    from_number,
                    buffered.pending_count,
                    buffered.collapsed,
                    buffered.dropped_oldest,
                )
                return PlainTextResponse("", status_code=200)

            task = TurnTask(
                from_number=from_number,
                body=body,
                inbound_sid=inbound_sid,
                pre_state=pre_state,
            )
            # Record this dispatch so rapid duplicate messages sent while this
            # turn is processing are collapsed instead of queued.
            _user_turn_buffer.record_dispatch(from_number, body)
            enqueued = turn_processor.submit(task)
            if not enqueued:
                _user_processing_guard.release(from_number)
                buffered = _user_turn_buffer.push(task)
                LOGGER.warning(
                    "Queue full. Buffered inbound sid=%s from=%s backlog=%d pending=%d collapsed=%s dropped_oldest=%s",
                    inbound_sid or "-",
                    from_number,
                    turn_processor.backlog_size(),
                    buffered.pending_count,
                    buffered.collapsed,
                    buffered.dropped_oldest,
                )
                return PlainTextResponse("", status_code=200)

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
        except Exception as exc:
            if acquired:
                _user_processing_guard.release(from_number)
            buffered = _user_turn_buffer.push(
                TurnTask(
                    from_number=from_number,
                    body=body,
                    inbound_sid=inbound_sid,
                    pre_state=pre_state,
                )
            )
            LOGGER.warning(
                "ACK-first fallback buffered sid=%s from=%s pending=%d error=%s",
                inbound_sid or "-",
                from_number,
                buffered.pending_count,
                exc,
            )
            return PlainTextResponse("", status_code=200)

    # TwiML/direct mode also stays ACK-first by buffering for async worker processing.
    buffered = _user_turn_buffer.push(
        TurnTask(
            from_number=from_number,
            body=body,
            inbound_sid=inbound_sid,
            pre_state=pre_state,
        )
    )
    LOGGER.info(
        "ACK-first buffered inbound sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
        inbound_sid or "-",
        from_number,
        buffered.pending_count,
        buffered.collapsed,
        buffered.dropped_oldest,
    )
    _submit_next_buffered_turn(from_number)
    return PlainTextResponse("", status_code=200)


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

    bot_identity = f"telegram_username:{_telegram_bot_username_runtime}" if _telegram_bot_username_runtime else ""
    _set_user_bot_identity(from_number, bot_identity)
    try:
        pre_state = session_manager.get_or_create(from_number).state
    except Exception:
        pre_state = "INIT"
    acquired = False
    try:
        acquired = _user_processing_guard.acquire(from_number)
        if not acquired:
            buffered = _user_turn_buffer.push(
                TurnTask(
                    from_number=from_number,
                    body=text,
                    inbound_sid=inbound_sid,
                    pre_state=pre_state,
                )
            )
            LOGGER.info(
                "Buffered inbound Telegram while processing sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
                inbound_sid or "-",
                from_number,
                buffered.pending_count,
                buffered.collapsed,
                buffered.dropped_oldest,
            )
            return PlainTextResponse("", status_code=200)

        task = TurnTask(
            from_number=from_number,
            body=text,
            inbound_sid=inbound_sid,
            pre_state=pre_state,
        )
        # Record this dispatch so rapid duplicate messages sent while this
        # turn is processing are collapsed instead of queued.
        _user_turn_buffer.record_dispatch(from_number, text)
        enqueued = turn_processor.submit(task)
        if not enqueued:
            _user_processing_guard.release(from_number)
            buffered = _user_turn_buffer.push(task)
            LOGGER.warning(
                "Queue full. Buffered inbound sid=%s from=%s backlog=%d pending=%d collapsed=%s dropped_oldest=%s",
                inbound_sid or "-",
                from_number,
                turn_processor.backlog_size(),
                buffered.pending_count,
                buffered.collapsed,
                buffered.dropped_oldest,
            )
            return PlainTextResponse("", status_code=200)

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
    except Exception as exc:
        if acquired:
            _user_processing_guard.release(from_number)
        buffered = _user_turn_buffer.push(
            TurnTask(
                from_number=from_number,
                body=text,
                inbound_sid=inbound_sid,
                pre_state=pre_state,
            )
        )
        LOGGER.warning(
            "ACK-first Telegram fallback buffered sid=%s from=%s pending=%d error=%s",
            inbound_sid or "-",
            from_number,
            buffered.pending_count,
            exc,
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


def _process_turn(from_number: str, body: str) -> Tuple[str, str, object]:
    user_lock = _get_user_lock(from_number)
    with user_lock:
        fsm = session_manager.get_or_create(from_number)
        identity = _get_user_bot_identity(from_number)
        if identity:
            fsm.bot_whatsapp_number = identity
        with _ollama_semaphore:  # serialize Ollama calls — only 1 inference at a time
            reply = fsm.handle(body)
        reply = reply[: settings.max_message_chars]
        session_manager.save(from_number, fsm)
        return reply, fsm.state, fsm


def _timeout_message(language: str, state: str) -> str:
    if language == "hi":
        return "Abhi processing mein delay ho raha hai. Kripaya thoda intezar karein, hum jaldi reply karenge."
    if language == "hinglish":
        return "Processing mein delay ho raha hai. Please wait, hum jaldi response denge."
    return "We are facing a delay while processing your request. Please wait, we will respond shortly."


def _handle_turn_timeout(task: TurnTask, exc: Exception) -> None:
    # Clean up overflow map entry so it does not leak on timeout.
    _pop_overflow_queue_id(task)
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
    _user_processing_guard.release(task.from_number)
    _submit_next_buffered_turn(task.from_number)
    queue_id = _pop_overflow_queue_id(task)
    if queue_id and conversation_repository:
        try:
            conversation_repository.mark_overflow_turn_done(queue_id=queue_id)
        except Exception:
            LOGGER.exception("Failed to mark overflow turn done sid=%s queue_id=%s", task.inbound_sid or "-", queue_id)


def _on_turn_failure(task: TurnTask, exc: Exception, will_retry: bool, backoff_seconds: float) -> None:
    if not will_retry:
        _user_processing_guard.release(task.from_number)
        _submit_next_buffered_turn(task.from_number)
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


def _submit_next_buffered_turn(from_number: str) -> None:
    next_task = _user_turn_buffer.pop_next(from_number)
    if not next_task:
        return
    acquired = _user_processing_guard.acquire(from_number)
    if not acquired:
        _user_turn_buffer.push_front(next_task)
        return
    if turn_processor.submit(next_task):
        return
    _user_processing_guard.release(from_number)
    _user_turn_buffer.push_front(next_task)


def _overflow_turn_poll_loop() -> None:
    if not conversation_repository:
        return
    claim_size = max(1, settings.queue_worker_count)
    sid_retention_days = max(1, int(os.getenv("INBOUND_SID_RETENTION_DAYS", "30")))
    sid_purge_interval_seconds = max(60, int(os.getenv("INBOUND_SID_PURGE_INTERVAL_SECONDS", "3600")))
    next_sid_purge_at = 0.0
    while not _overflow_poll_stop.is_set():
        try:
            now_monotonic = time.monotonic()
            if now_monotonic >= next_sid_purge_at:
                try:
                    deleted = conversation_repository.purge_old_message_sids(retention_days=sid_retention_days)
                    if deleted:
                        LOGGER.info(
                            "Purged old inbound_message_sids rows=%d retention_days=%d",
                            deleted,
                            sid_retention_days,
                        )
                except Exception:
                    LOGGER.exception("Failed to purge inbound_message_sids")
                next_sid_purge_at = now_monotonic + sid_purge_interval_seconds
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


def _doctor_cache_invalidation_loop() -> None:
    if not scheduling_repository:
        return
    claim_size = max(1, min(100, settings.queue_worker_count * 10))
    while not _cache_inv_stop.is_set():
        try:
            events = scheduling_repository.claim_cache_invalidation_events(
                limit=claim_size,
                worker_id=_cache_inv_worker_id,
            )
            if not events:
                _cache_inv_stop.wait(0.8)
                continue
            for event in events:
                try:
                    scheduling_repository.process_cache_invalidation_event(event)
                    scheduling_repository.mark_cache_invalidation_done(event.queue_id)
                except Exception:
                    LOGGER.exception(
                        "Doctor cache invalidation failed queue_id=%s entity=%s doctor=%s clinic=%s",
                        event.queue_id,
                        event.entity_type,
                        event.doctor_id,
                        event.clinic_id,
                    )
                    scheduling_repository.release_cache_invalidation(event.queue_id)
        except Exception as exc:
            LOGGER.warning("Doctor cache invalidation poll failed error=%s", exc)
            _cache_inv_stop.wait(1.0)


def _get_user_lock(user_id: str) -> Lock:
    uid = (user_id or "").strip()
    if not uid:
        return _user_lock_stripes[0]
    return _user_lock_stripes[hash(uid) % _user_lock_stripes_count]


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
    LOGGER.warning(
        "WhatsApp document send degraded to text (Twilio requires a public media URL). "
        "to=%s file=%s — configure a media hosting endpoint to enable file delivery.",
        to_number,
        os.path.basename(file_path),
    )
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
            continue
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
        dates = fsm._date_options()
        if len(dates) < 3:
            return {}
        d1, d2, d3 = dates[:3]
        return {"1": d1, "2": d2, "3": d3}
    if state == "ASK_TIME":
        slots = fsm._suggested_slots()
        if len(slots) < 3:
            return {}
        return {"1": slots[0], "2": slots[1], "3": slots[2]}
    return {}
