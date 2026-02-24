from dataclasses import dataclass
import os


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_whatsapp_sender(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("whatsapp:"):
        return raw
    return f"whatsapp:{raw}"


@dataclass(frozen=True)
class Settings:
    app_name: str = "whatsapp-appointment-bot"
    log_level: str = "INFO"

    llm_provider: str = "ollama"
    llm_model: str = "qwen3:1.7b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_timeout_seconds: float = 30.0
    ollama_auto_start: bool = True
    ollama_auto_pull: bool = True
    ollama_startup_timeout_seconds: float = 30.0

    mixed_response_language: str = "auto"
    enable_llm_polish: bool = True
    enable_db_booking: bool = True

    enable_twilio_signature_validation: bool = False
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_use_rest_responses: bool = True
    twilio_template_patient_type_sid: str = ""
    twilio_template_gender_sid: str = ""
    twilio_template_phone_choice_sid: str = ""
    twilio_template_clinic_sid: str = ""
    twilio_template_reason_sid: str = ""
    twilio_template_date_sid: str = ""
    twilio_template_time_sid: str = ""
    processing_timeout_seconds: float = 2.5
    twilio_send_retries: int = 2
    twilio_status_callback_url: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_bot_username: str = ""
    queue_worker_count: int = 3
    queue_max_size: int = 60
    queue_retry_attempts: int = 2
    queue_busy_threshold: int = 3
    queue_overflow_requeue_attempts: int = 30
    queue_overflow_requeue_backoff_seconds: float = 1.0

    admin_api_key: str = ""
    admin_api_rate_limit_per_minute: int = 60
    admin_auth_token_ttl_minutes: int = 480

    session_ttl_minutes: int = 120
    max_message_chars: int = 1500
    automation_enabled: bool = True
    doctor_reminder_enabled: bool = True
    doctor_reminder_interval_seconds: int = 60
    doctor_reminder_lead_minutes: int = 10
    doctor_reminder_window_seconds: int = 30



def load_settings() -> Settings:
    twilio_sender = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
    return Settings(
        app_name=os.getenv("APP_NAME", "whatsapp-appointment-bot"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
        llm_model=os.getenv("LLM_MODEL", "qwen3:1.7b"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "33")),
        ollama_auto_start=_as_bool(os.getenv("OLLAMA_AUTO_START", "true")),
        ollama_auto_pull=_as_bool(os.getenv("OLLAMA_AUTO_PULL", "true")),
        ollama_startup_timeout_seconds=float(
            os.getenv("OLLAMA_STARTUP_TIMEOUT_SECONDS", "30")
        ),
        mixed_response_language=os.getenv("MIXED_RESPONSE_LANGUAGE", "auto"),
        enable_llm_polish=_as_bool(os.getenv("ENABLE_LLM_POLISH", "true")),
        enable_db_booking=_as_bool(os.getenv("ENABLE_DB_BOOKING", "true")),
        enable_twilio_signature_validation=_as_bool(
            os.getenv("ENABLE_TWILIO_SIGNATURE_VALIDATION", "false")
        ),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_whatsapp_from=_normalize_whatsapp_sender(twilio_sender),
        twilio_use_rest_responses=_as_bool(os.getenv("TWILIO_USE_REST_RESPONSES", "true")),
        twilio_template_patient_type_sid=os.getenv("TWILIO_TEMPLATE_PATIENT_TYPE_SID", ""),
        twilio_template_gender_sid=os.getenv("TWILIO_TEMPLATE_GENDER_SID", ""),
        twilio_template_phone_choice_sid=os.getenv("TWILIO_TEMPLATE_PHONE_CHOICE_SID", ""),
        twilio_template_clinic_sid=os.getenv("TWILIO_TEMPLATE_CLINIC_SID", ""),
        twilio_template_reason_sid=os.getenv("TWILIO_TEMPLATE_REASON_SID", ""),
        twilio_template_date_sid=os.getenv("TWILIO_TEMPLATE_DATE_SID", ""),
        twilio_template_time_sid=os.getenv("TWILIO_TEMPLATE_TIME_SID", ""),
        processing_timeout_seconds=float(os.getenv("PROCESSING_TIMEOUT_SECONDS", "2.5")),
        twilio_send_retries=int(os.getenv("TWILIO_SEND_RETRIES", "2")),
        twilio_status_callback_url=os.getenv("TWILIO_STATUS_CALLBACK_URL", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip(),
        telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
        queue_worker_count=int(os.getenv("QUEUE_WORKER_COUNT", "3")),
        queue_max_size=int(os.getenv("QUEUE_MAX_SIZE", "60")),
        queue_retry_attempts=int(os.getenv("QUEUE_RETRY_ATTEMPTS", "2")),
        queue_busy_threshold=int(os.getenv("QUEUE_BUSY_THRESHOLD", "3")),
        queue_overflow_requeue_attempts=int(os.getenv("QUEUE_OVERFLOW_REQUEUE_ATTEMPTS", "30")),
        queue_overflow_requeue_backoff_seconds=float(os.getenv("QUEUE_OVERFLOW_REQUEUE_BACKOFF_SECONDS", "1.0")),
        admin_api_key=os.getenv("ADMIN_API_KEY", "").strip(),
        admin_api_rate_limit_per_minute=int(os.getenv("ADMIN_API_RATE_LIMIT_PER_MINUTE", "60")),
        admin_auth_token_ttl_minutes=int(os.getenv("ADMIN_AUTH_TOKEN_TTL_MINUTES", "480")),
        session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "120")),
        max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "1500")),
        automation_enabled=_as_bool(os.getenv("AUTOMATION_ENABLED", "true")),
        doctor_reminder_enabled=_as_bool(os.getenv("DOCTOR_REMINDER_ENABLED", "true")),
        doctor_reminder_interval_seconds=int(os.getenv("DOCTOR_REMINDER_INTERVAL_SECONDS", "60")),
        doctor_reminder_lead_minutes=int(os.getenv("DOCTOR_REMINDER_LEAD_MINUTES", "10")),
        doctor_reminder_window_seconds=int(os.getenv("DOCTOR_REMINDER_WINDOW_SECONDS", "30")),
    )
