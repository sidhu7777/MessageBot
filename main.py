import logging
import json
import os
import time
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import FastAPI
from fastapi.responses import FileResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from dotenv import load_dotenv

from src.automation import AutomationScheduler
from src.api.qr_routes import register_qr_routes
from src.api.evolution_webhook_routes import register_evolution_webhook_routes
from src.api.whatsapp_web_routes import register_whatsapp_web_routes
from src.api.webhooks import register_webhook_routes
from src.config import load_settings
from src.db_store import (
    channel_account_repository_from_env,
    conversation_repository_from_env,
    repositories_from_env,
)
from src.llm.client import LLMClient
from src.ollama_runtime import OllamaStartupError, ensure_ollama_ready
from src.runtime import PersistentMessageSidStore, TurnQueueProcessor, TurnTask
from src.runtime.background_workers import (
    run_doctor_cache_invalidation_loop,
    run_overflow_turn_poll_loop,
)
from src.runtime.kafka_notification_bridge import KafkaNotificationBridge
from src.runtime.kafka_turn_bridge import KafkaTurnBridge
from src.runtime.user_turn_buffer import UserTurnBuffer
from src.runtime.user_processing_guard import UserProcessingGuard, build_redis_client_from_env
from src.runtime.channel_delivery import ChannelDelivery
from src.session_store import SessionManager
from src.chat_logger import log_event, extract_chat_id
from src.evolution import EvolutionApiClient, EvolutionApiSettings, EvolutionAutoResponsePolicy
from src.qr import QrCheckinService
from src.repositories.evolution_repository import EvolutionRepository
from src.repositories.notification_repository import NotificationEvent


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
channel_account_repository = channel_account_repository_from_env()
qr_checkin_service = (
    QrCheckinService(booking_repository=booking_repository, scheduling_repository=scheduling_repository)
    if booking_repository and scheduling_repository
    else None
)
evolution_repository = EvolutionRepository(booking_repository._config) if booking_repository else None
if evolution_repository:
    try:
        evolution_repository.ensure_schema()
    except Exception as exc:
        LOGGER.warning("Evolution binding schema ensure failed error=%s", exc)

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
channel_delivery = ChannelDelivery(
    settings=settings,
    twilio_client=twilio_client,
    logger=LOGGER,
    log_event_fn=log_event,
    extract_chat_id_fn=extract_chat_id,
    channel_account_lookup_fn=lambda account_id: _lookup_channel_account_credentials(account_id),
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
_user_route_context: Dict[str, dict] = {}
_user_route_context_lock = Lock()
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
evolution_policy = EvolutionAutoResponsePolicy(
    redis_client=_redis_client,
    session_window_seconds=settings.evolution_session_window_seconds,
    key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
)
evolution_api_client = EvolutionApiClient(
    EvolutionApiSettings(
        base_url=settings.evolution_api_base_url,
        api_key=settings.evolution_api_key,
        send_text_path_template=settings.evolution_send_text_path_template,
    )
)
session_manager.redis_client = _redis_client
if booking_repository:
    booking_repository.set_redis_client(
        _redis_client,
        key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
    )
if scheduling_repository:
    scheduling_repository.set_redis_client(_redis_client)
    scheduling_repository.set_cache_config(
        ttl_seconds=int(os.getenv("REDIS_DOCTOR_CACHE_TTL_SECONDS", "3600")),
        key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
    )
if qr_checkin_service:
    qr_checkin_service.set_redis_client(
        _redis_client,
        key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
    )
    qr_checkin_service.set_cache_config(
        ttl_seconds=int(os.getenv("REDIS_HOSPITAL_CACHE_TTL_SECONDS", "300")),
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


def _set_user_route_context(user_id: str, context: dict) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    with _user_route_context_lock:
        if context:
            _user_route_context[uid] = dict(context)
        else:
            _user_route_context.pop(uid, None)
    _evict_dict_if_needed(_user_route_context, _user_route_context_lock)


def _get_user_route_context(user_id: str) -> dict:
    uid = (user_id or "").strip()
    if not uid:
        return {}
    with _user_route_context_lock:
        return dict(_user_route_context.get(uid) or {})


def _lookup_channel_account_credentials(channel_account_id: int) -> dict:
    if not channel_account_repository:
        return {}
    try:
        account = channel_account_repository.get_account_by_id(int(channel_account_id))
        if not account:
            return {}
        creds = account.credentials()
        if not isinstance(creds, dict):
            creds = {}
        result = dict(creds)
        result["_provider"] = str(account.provider or "").strip().lower()
        result["_channel"] = str(account.channel or "").strip().lower()
        result["provider"] = result.get("provider") or result["_provider"]
        result["sender_identity"] = str(account.sender_identity or "").strip()
        if account.provider == "telegram":
            token = str(result.get("telegram_bot_token") or "").strip()
            if token:
                result["telegram_bot_token"] = token
        return result
    except Exception:
        return {}


def _resolve_channel_account_id_for_doctor(channel: str, doctor_id: int) -> Optional[int]:
    if not channel_account_repository:
        return None
    try:
        account = channel_account_repository.resolve_account_for_doctor(
            channel=(channel or "").strip().lower(),
            doctor_id=int(doctor_id),
        )
        if not account:
            return None
        return int(account.channel_account_id)
    except Exception:
        return None

_base_turn_processor = TurnQueueProcessor(
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
turn_processor = KafkaTurnBridge(
    settings=settings,
    logger=LOGGER,
    turn_processor=_base_turn_processor,
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
    settings=settings,
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
    resolve_channel_account_id_fn=lambda channel, doctor_id: _resolve_channel_account_id_for_doctor(channel, doctor_id),
)
automation_scheduler._notification_bridge = KafkaNotificationBridge(
    settings=settings,
    logger=LOGGER,
    process_event_fn=automation_scheduler._process_notification_event,
    event_cls=NotificationEvent,
)

@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    await startup_validation()
    try:
        yield
    finally:
        await shutdown_workers()


app = FastAPI(title=settings.app_name, lifespan=_app_lifespan)
_DAPTO_LOGO_PATH = Path(__file__).resolve().parent / "Dapto_logo.jpeg"


async def startup_validation() -> None:
    global _telegram_bot_username_runtime, _overflow_poll_thread, _cache_inv_thread
    if settings.enable_db_booking and booking_repository:
        try:
            booking_repository.ensure_appointment_columns()
        except Exception as exc:
            LOGGER.warning("Appointment column ensure failed: %s", exc)
        try:
            booking_repository.ensure_notification_schema()
        except Exception as exc:
            LOGGER.warning("Notification schema ensure failed: %s", exc)
    if conversation_repository:
        try:
            conversation_repository.ensure_schema()
        except Exception as exc:
            LOGGER.warning("Conversation schema ensure failed: %s", exc)
    if qr_checkin_service:
        try:
            qr_checkin_service.ensure_schema()
        except Exception as exc:
            LOGGER.warning("QR schema ensure failed: %s", exc)
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
    else:
        LOGGER.info("Telegram bot username runtime resolution skipped; per-account sender identity is required.")
    if settings.llm_provider.lower() != "ollama":
        return
    try:
        # TEMPORARILY DISABLED - Ollama check causes startup to fail when Ollama not available
        # ensure_ollama_ready(
        #     base_url=settings.ollama_base_url,
        #     model=settings.llm_model,
        #     auto_start=settings.ollama_auto_start,
        #     auto_pull=settings.ollama_auto_pull,
        #     timeout_seconds=settings.ollama_startup_timeout_seconds,
        # )
        LOGGER.info("Ollama startup validation skipped - ensure Ollama is running manually")
        LOGGER.info("To start: ollama serve on host machine")
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


@app.get("/branding/dapto-logo")
async def dapto_logo() -> FileResponse:
    return FileResponse(_DAPTO_LOGO_PATH, media_type="image/jpeg")


register_qr_routes(
    app,
    qr_checkin_service=qr_checkin_service,
    logger=LOGGER,
    log_event_fn=log_event,
)

register_whatsapp_web_routes(
    app,
    booking_repository=booking_repository,
    scheduling_repository=scheduling_repository,
    logger=LOGGER,
)

register_evolution_webhook_routes(
    app,
    settings=settings,
    logger=LOGGER,
    evolution_repository=evolution_repository,
    evolution_policy=evolution_policy,
    evolution_api_client=evolution_api_client,
)


register_webhook_routes(
    app,
    settings=settings,
    logger=LOGGER,
    request_validator=lambda: request_validator,
    sid_store=lambda: sid_store,
    session_manager=lambda: session_manager,
    twilio_client=lambda: twilio_client,
    turn_processor=lambda: turn_processor,
    booking_repository=lambda: booking_repository,
    channel_account_repository=lambda: channel_account_repository,
    user_processing_guard=lambda: _user_processing_guard,
    user_turn_buffer=lambda: _user_turn_buffer,
    route_cache_client=lambda: _redis_client,
    set_user_bot_identity=_set_user_bot_identity,
    set_user_route_context=_set_user_route_context,
    submit_next_buffered_turn=lambda from_number: _submit_next_buffered_turn(from_number),
    get_telegram_bot_username=lambda: _telegram_bot_username_runtime,
)


def _process_turn(from_number: str, body: str) -> Tuple[str, str, object]:
    _cid_log = extract_chat_id(from_number)
    user_lock = _get_user_lock(from_number)
    with user_lock:
        try:
            log_event(_cid_log, "TURN_START", text=body[:80])
        except Exception:
            pass
        _t_turn = time.perf_counter()
        fsm = session_manager.get_or_create(from_number)
        _pre_state = fsm.state
        route_ctx = _get_user_route_context(from_number)
        if route_ctx:
            incoming_channel_account_id = (
                int(route_ctx["channel_account_id"]) if route_ctx.get("channel_account_id") is not None else None
            )
            incoming_doctor_id = int(route_ctx["doctor_id"]) if route_ctx.get("doctor_id") is not None else None
            incoming_admin_id = int(route_ctx["admin_id"]) if route_ctx.get("admin_id") is not None else None
            incoming_channel_provider = str(route_ctx.get("channel_provider") or "").strip()

            # Lock routing to first resolved account/doctor for the active session.
            # Prevents accidental reassignment during an in-flight conversation.
            if fsm.channel_account_id is None and incoming_channel_account_id is not None:
                fsm.channel_account_id = incoming_channel_account_id
            if fsm.doctor_id is None and incoming_doctor_id is not None:
                fsm.doctor_id = incoming_doctor_id
            if fsm.admin_id is None and incoming_admin_id is not None:
                fsm.admin_id = incoming_admin_id
            if not fsm.channel_provider and incoming_channel_provider:
                fsm.channel_provider = incoming_channel_provider
        identity = _get_user_bot_identity(from_number)
        if identity:
            fsm.bot_whatsapp_number = identity
        _t_fsm = time.perf_counter()
        with _ollama_semaphore:  # serialize Ollama calls — only 1 inference at a time
            reply = fsm.handle(body)
        _fsm_ms = int((time.perf_counter() - _t_fsm) * 1000)
        try:
            log_event(_cid_log, "FSM_HANDLED", pre_state=_pre_state, post_state=fsm.state, fsm_ms=_fsm_ms, reply=reply[:80])
        except Exception:
            pass
        reply = reply[: settings.max_message_chars]
        _t_save = time.perf_counter()
        session_manager.save(from_number, fsm)
        _save_ms = int((time.perf_counter() - _t_save) * 1000)
        _total_ms = int((time.perf_counter() - _t_turn) * 1000)
        try:
            log_event(_cid_log, "TURN_END", total_ms=_total_ms, save_ms=_save_ms, post_state=fsm.state)
        except Exception:
            pass
        return reply, fsm.state, fsm


def _timeout_message(language: str) -> str:
    from src.messages.templates import get_message

    return get_message(language, "timeout_processing_delay")


def _handle_turn_timeout(task: TurnTask, exc: Exception) -> None:
    # Clean up overflow map entry so it does not leak on timeout.
    _pop_overflow_queue_id(task)
    try:
        fsm = session_manager.get_or_create(task.from_number)
        timeout_msg = _timeout_message(fsm.response_language)
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
    run_overflow_turn_poll_loop(
        conversation_repository=conversation_repository,
        turn_processor=turn_processor,
        overflow_poll_stop=_overflow_poll_stop,
        settings=settings,
        overflow_worker_id=_overflow_worker_id,
        logger=LOGGER,
        sid_retention_days=max(1, int(os.getenv("INBOUND_SID_RETENTION_DAYS", "30"))),
        sid_purge_interval_seconds=max(60, int(os.getenv("INBOUND_SID_PURGE_INTERVAL_SECONDS", "3600"))),
        track_overflow_task=_track_overflow_task,
    )


def _doctor_cache_invalidation_loop() -> None:
    run_doctor_cache_invalidation_loop(
        scheduling_repository=scheduling_repository,
        cache_inv_stop=_cache_inv_stop,
        settings=settings,
        cache_inv_worker_id=_cache_inv_worker_id,
        logger=LOGGER,
    )


def _get_user_lock(user_id: str) -> Lock:
    uid = (user_id or "").strip()
    if not uid:
        return _user_lock_stripes[0]
    return _user_lock_stripes[hash(uid) % _user_lock_stripes_count]


def _send_channel_response(to_number: str, reply_text: str, fsm_state: str, fsm, inbound_sid: str = "") -> None:
    channel_delivery.send_channel_response(
        to_number=to_number,
        reply_text=reply_text,
        fsm_state=fsm_state,
        fsm=fsm,
        inbound_sid=inbound_sid,
    )


def _send_plain_channel_message(to_number: str, body: str, inbound_sid: str = "") -> str:
    return channel_delivery.send_plain_channel_message(
        to_number=to_number,
        body=body,
        inbound_sid=inbound_sid,
    )


def _send_plain_channel_document(to_number: str, file_path: str, caption: str = "", inbound_sid: str = "") -> None:
    channel_delivery.send_plain_channel_document(
        to_number=to_number,
        file_path=file_path,
        caption=caption,
        inbound_sid=inbound_sid,
    )


def _resolve_telegram_bot_username() -> str:
    return channel_delivery.resolve_telegram_bot_username()
