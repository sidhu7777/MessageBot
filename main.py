import logging
import json
import os
import time
import threading
import uuid
import html as html_escape
import asyncio
from contextlib import asynccontextmanager
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from dotenv import load_dotenv

from src.automation import AutomationScheduler
from src.api.webhooks import register_webhook_routes
from src.config import load_settings
from src.db_store import conversation_repository_from_env, repositories_from_env
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
from src.qr import QrCheckinService
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
qr_checkin_service = (
    QrCheckinService(booking_repository=booking_repository, scheduling_repository=scheduling_repository)
    if booking_repository and scheduling_repository
    else None
)

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
# Maximum number of per-user entries kept in the identity/generation dicts.
# Protects against unbounded memory growth with many unique users.
_PER_USER_DICT_MAX = max(5000, int(os.getenv("PER_USER_DICT_MAX", "20000")))
_qr_page_lookup_timeout_seconds = max(0.2, float(os.getenv("QR_PAGE_LOOKUP_TIMEOUT_SECONDS", "2.0")))


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


def _qr_page_html(*, doctor_id: int, clinic_id: int, doctor_name: str, clinic_name: str) -> str:
    doctor_name_safe = html_escape.escape(doctor_name or "Doctor")
    clinic_name_safe = html_escape.escape(clinic_name or "Clinic")
    qr_base_url = (os.getenv("QR_BASE_URL", "") or "").strip().rstrip("/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Clinic QR Check-in</title>
  <style>
    :root {{
      --bg: linear-gradient(140deg, #f6f7f2 0%, #e5efe0 60%, #d7e7dc 100%);
      --card: #ffffff;
      --ink: #1d2a23;
      --muted: #5b6e62;
      --accent: #0f766e;
      --accent-soft: #d3f2ee;
      --ok: #0a7a4f;
      --warn: #a85810;
      --danger: #9f2d2d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background: var(--bg);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 18px;
    }}
    .card {{
      width: min(760px, 100%);
      background: var(--card);
      border-radius: 20px;
      box-shadow: 0 20px 50px rgba(8, 38, 34, 0.14);
      overflow: hidden;
      border: 1px solid #e4efe8;
    }}
    .hero {{
      padding: 28px 24px 12px 24px;
      background:
        radial-gradient(circle at 85% 15%, #c5f3ea 0, rgba(197,243,234,0) 46%),
        radial-gradient(circle at 20% 0%, #ebf9f4 0, rgba(235,249,244,0) 52%);
    }}
    .kicker {{
      display: inline-block;
      background: var(--accent-soft);
      color: #0d645d;
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 12px 0 8px 0;
      font-size: 28px;
      line-height: 1.2;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 15px;
    }}
    .form-wrap {{
      padding: 20px 24px 24px 24px;
      display: grid;
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-weight: 600;
      font-size: 14px;
    }}
    select, input {{
      width: 100%;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 12px 12px;
      font-size: 15px;
      outline: none;
      transition: border-color .2s, box-shadow .2s;
    }}
    select:focus, input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15,118,110,0.13);
    }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    @media (min-width: 640px) {{
      .grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    button {{
      margin-top: 4px;
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      color: white;
      background: linear-gradient(135deg, #0f766e, #0b5a53);
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{
      opacity: .7;
      cursor: not-allowed;
    }}
    .result {{
      border: 1px solid #dce9e2;
      background: #f8fcfa;
      border-radius: 12px;
      padding: 12px;
      min-height: 50px;
      font-size: 14px;
      white-space: pre-wrap;
    }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .err {{ color: var(--danger); }}
  </style>
</head>
<body>
  <section class="card">
    <div class="hero">
      <span class="kicker" id="kicker">QR Check-in</span>
      <h1 id="title">Welcome to Dr. {doctor_name_safe} clinic</h1>
      <p class="subtitle" id="subtitle">Clinic: {clinic_name_safe}</p>
    </div>
    <form class="form-wrap" id="checkinForm">
      <label>
        <span id="langLabel">Select language</span>
        <select id="language">
          <option value="en">English</option>
          <option value="hi">हिंदी</option>
          <option value="hinglish">Hinglish</option>
        </select>
      </label>
      <div class="grid">
        <label>
          <span id="nameLabel">Full Name</span>
          <input id="patientName" maxlength="120" required />
        </label>
        <label>
          <span id="phoneLabel">Phone Number</span>
          <input id="phoneNumber" maxlength="20" required />
        </label>
      </div>
      <button id="submitBtn" type="submit">Submit</button>
      <div id="result" class="result"></div>
      <input type="hidden" id="doctorId" value="{doctor_id}" />
      <input type="hidden" id="clinicId" value="{clinic_id}" />
    </form>
  </section>

  <script>
    const t = {{
      en: {{
        title: "Welcome to Dr. {doctor_name_safe} clinic",
        subtitle: "Clinic: {clinic_name_safe}",
        langLabel: "Select language",
        nameLabel: "Full Name",
        phoneLabel: "Phone Number",
        submit: "Submit",
      }},
      hi: {{
        title: "Dr. {doctor_name_safe} क्लिनिक में आपका स्वागत है",
        subtitle: "क्लिनिक: {clinic_name_safe}",
        langLabel: "भाषा चुनें",
        nameLabel: "पूरा नाम",
        phoneLabel: "फोन नंबर",
        submit: "जमा करें",
      }},
      hinglish: {{
        title: "Dr. {doctor_name_safe} clinic mein aapka swagat hai",
        subtitle: "Clinic: {clinic_name_safe}",
        langLabel: "Language select kariye",
        nameLabel: "Full Name",
        phoneLabel: "Phone Number",
        submit: "Submit kariye",
      }},
    }};

    function applyLanguage(lang) {{
      const d = t[lang] || t.en;
      document.getElementById("title").textContent = d.title;
      document.getElementById("subtitle").textContent = d.subtitle;
      document.getElementById("langLabel").textContent = d.langLabel;
      document.getElementById("nameLabel").textContent = d.nameLabel;
      document.getElementById("phoneLabel").textContent = d.phoneLabel;
      document.getElementById("submitBtn").textContent = d.submit;
    }}

    document.getElementById("language").addEventListener("change", (e) => applyLanguage(e.target.value));
    applyLanguage("en");

    document.getElementById("checkinForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const qrBaseUrl = {json.dumps(qr_base_url)};
      const submitUrl = qrBaseUrl ? `${{qrBaseUrl}}/qr/checkin/submit` : "/qr/checkin/submit";
      const result = document.getElementById("result");
      const btn = document.getElementById("submitBtn");
      btn.disabled = true;
      result.className = "result";
      result.textContent = "Submitting...";
      try {{
        const payload = {{
          doctor_id: Number(document.getElementById("doctorId").value),
          clinic_id: Number(document.getElementById("clinicId").value),
          patient_name: document.getElementById("patientName").value,
          phone_number: document.getElementById("phoneNumber").value,
          language: document.getElementById("language").value,
        }};
        const resp = await fetch(submitUrl, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        const renderResultMessage = (message) => {{
          const safe = String(message || "Done.")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          const withBold = safe.replace(
            /(Patient ID:\s*\d+\.?)/gi,
            "<strong>$1</strong>"
          );
          result.innerHTML = withBold.replace(/\n/g, "<br>");
        }};
        if (!resp.ok) {{
          result.classList.add("err");
          result.textContent = data.detail || "Request failed.";
      }} else {{
          const status = data.status || "";
          if (status === "booked") result.classList.add("ok");
          else if (status === "overflow" || status === "active_booking") result.classList.add("warn");
          else result.classList.add("err");
          renderResultMessage(data.message || "Done.");
        }}
      }} catch (_err) {{
        result.classList.add("err");
        result.textContent = "Unable to submit right now. Please try again.";
      }} finally {{
        btn.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""


@app.get("/qr/checkin", response_class=HTMLResponse)
async def qr_checkin_page(doctor_id: int, clinic_id: int):
    if not qr_checkin_service:
        return HTMLResponse("<h3>QR check-in is not configured.</h3>", status_code=503)
    doctor_name, clinic_name = "Doctor", "Clinic"
    try:
        doctor_name, clinic_name = await asyncio.wait_for(
            run_in_threadpool(
                qr_checkin_service.resolve_doctor_and_clinic,
                doctor_id=doctor_id,
                clinic_id=clinic_id,
            ),
            timeout=_qr_page_lookup_timeout_seconds,
        )
    except Exception as exc:
        LOGGER.warning(
            "QR page name lookup fallback doctor_id=%s clinic_id=%s error=%s",
            doctor_id,
            clinic_id,
            exc,
        )
    return HTMLResponse(
        _qr_page_html(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            doctor_name=doctor_name,
            clinic_name=clinic_name,
        )
    )


@app.post("/qr/checkin/submit")
async def qr_checkin_submit(request: Request):
    if not qr_checkin_service:
        return JSONResponse({"detail": "QR check-in is not configured."}, status_code=503)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid QR check-in request payload."}, status_code=400)

    patient_name = str(payload.get("patient_name") or "").strip()
    phone_number = str(payload.get("phone_number") or "").strip()
    qr_chat_id = f"qr_{''.join(ch for ch in phone_number if ch.isdigit()) or 'unknown'}"
    log_event(
        qr_chat_id,
        "QR_SUBMIT_RECEIVED",
        doctor_id=payload.get("doctor_id"),
        clinic_id=payload.get("clinic_id"),
        patient_name=patient_name[:80],
        phone=phone_number,
    )
    try:
        doctor_id = int(payload.get("doctor_id"))
        clinic_id = int(payload.get("clinic_id"))
    except Exception:
        log_event(
            qr_chat_id,
            "QR_SUBMIT_REJECTED",
            reason="invalid_doctor_or_clinic_id",
            doctor_id=payload.get("doctor_id"),
            clinic_id=payload.get("clinic_id"),
        )
        return JSONResponse({"detail": "Invalid doctor_id/clinic_id."}, status_code=400)

    result = await run_in_threadpool(
        qr_checkin_service.process_checkin,
        doctor_id=doctor_id,
        clinic_id=clinic_id,
        patient_name=patient_name,
        phone=phone_number,
    )
    status_code = 200 if result.status in {"booked", "overflow", "active_booking"} else 400
    event_name = "QR_SUBMIT_SUCCEEDED" if status_code == 200 else "QR_SUBMIT_FAILED"
    log_event(
        qr_chat_id,
        event_name,
        status=result.status,
        message=result.message[:120],
        booking_id=result.booking_id,
        appointment_date=result.appointment_date,
        appointment_time=result.appointment_time,
        clinic_name=result.clinic_name,
        doctor_name=result.doctor_name,
    )
    if status_code != 200:
        LOGGER.warning(
            "QR submit failed doctor_id=%s clinic_id=%s phone=%s status=%s message=%s",
            doctor_id,
            clinic_id,
            phone_number,
            result.status,
            result.message,
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
        },
        status_code=status_code,
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
    user_processing_guard=lambda: _user_processing_guard,
    user_turn_buffer=lambda: _user_turn_buffer,
    set_user_bot_identity=_set_user_bot_identity,
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
