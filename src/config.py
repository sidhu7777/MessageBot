from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


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
    llm_model: str = "qwen3:0.6b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_timeout_seconds: float = 30.0
    ollama_auto_start: bool = True
    ollama_auto_pull: bool = True
    ollama_startup_timeout_seconds: float = 30.0

    mixed_response_language: str = "auto"
    enable_llm_polish: bool = True
    enable_db_booking: bool = True

    enable_twilio_signature_validation: bool = False
    whatsapp_provider: str = "auto"
    whatsapp_api_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_webhook_verify_token: str = ""
    meta_app_secret: str = ""
    enable_meta_signature_validation: bool = False
    whatsapp_graph_api_version: str = "v21.0"
    whatsapp_webhook_url: str = ""
    infobip_api_key: str = ""
    infobip_base_url: str = ""
    infobip_whatsapp_number: str = ""
    infobip_webhook_url: str = ""
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
    telegram_webhook_url: str = ""
    twilio_webhook_url: str = ""
    queue_worker_count: int = 3
    queue_max_size: int = 60
    queue_retry_attempts: int = 2
    queue_busy_threshold: int = 3
    queue_overflow_requeue_attempts: int = 30
    queue_overflow_requeue_backoff_seconds: float = 1.0
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = ""
    kafka_turn_topic: str = "msgbot.turns"
    kafka_turn_consumer_group: str = "msgbot-turn-workers"
    kafka_poll_timeout_ms: int = 1000
    kafka_notification_topic: str = "msgbot.notifications"
    kafka_notification_consumer_group: str = "msgbot-notification-workers"

    admin_api_key: str = ""
    admin_api_rate_limit_per_minute: int = 60
    admin_auth_token_ttl_minutes: int = 480

    session_ttl_minutes: int = 10
    max_message_chars: int = 1500
    automation_enabled: bool = True
    doctor_reminder_enabled: bool = True
    doctor_reminder_interval_seconds: int = 60
    doctor_reminder_lead_minutes: int = 10
    doctor_reminder_window_seconds: int = 30



def load_settings() -> Settings:
    twilio_sender = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
    whatsapp_api_token = (
        os.getenv("WHATSAPP_API_TOKEN", "").strip()
        or os.getenv("WHATSAPP_BOT_TOKEN", "").strip()
    )
    return Settings(
        app_name=os.getenv("APP_NAME", "whatsapp-appointment-bot"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
        llm_model=os.getenv("LLM_MODEL_NAME", "").strip() or "qwen3:0.6b",
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
        whatsapp_provider=os.getenv("WHATSAPP_PROVIDER", "auto").strip().lower() or "auto",
        whatsapp_api_token=whatsapp_api_token,
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        whatsapp_business_account_id=os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip(),
        whatsapp_webhook_verify_token=(
            os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip()
            or os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
            or os.getenv("WEBHOOK_VERIFY_TOKEN", "").strip()
        ),
        meta_app_secret=os.getenv("META_APP_SECRET", "").strip(),
        enable_meta_signature_validation=_as_bool(
            os.getenv("ENABLE_META_SIGNATURE_VALIDATION", "false")
        ),
        whatsapp_graph_api_version=os.getenv("WHATSAPP_GRAPH_API_VERSION", "v21.0").strip() or "v21.0",
        whatsapp_webhook_url=(
            os.getenv("WHATSAPP_WEBHOOK_URL", "").strip()
            or os.getenv("WEBHOOK_BASE_URL", "").strip()
        ),
        infobip_api_key=os.getenv("INFOBIP_API_KEY", "").strip(),
        infobip_base_url=os.getenv("INFOBIP_BASE_URL", "").strip(),
        infobip_whatsapp_number=os.getenv("INFOBIP_WHATSAPP_NUMBER", "").strip(),
        infobip_webhook_url=os.getenv("INFOBIP_WEBHOOK_URL", "").strip(),
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
        telegram_webhook_url=os.getenv("TELEGRAM_WEBHOOK_URL", "").strip(),
        twilio_webhook_url=os.getenv("TWILIO_WEBHOOK_URL", "").strip(),
        queue_worker_count=int(os.getenv("QUEUE_WORKER_COUNT", "3")),
        queue_max_size=int(os.getenv("QUEUE_MAX_SIZE", "60")),
        queue_retry_attempts=int(os.getenv("QUEUE_RETRY_ATTEMPTS", "2")),
        queue_busy_threshold=int(os.getenv("QUEUE_BUSY_THRESHOLD", "3")),
        queue_overflow_requeue_attempts=int(os.getenv("QUEUE_OVERFLOW_REQUEUE_ATTEMPTS", "30")),
        queue_overflow_requeue_backoff_seconds=float(os.getenv("QUEUE_OVERFLOW_REQUEUE_BACKOFF_SECONDS", "1.0")),
        kafka_enabled=_as_bool(os.getenv("KAFKA_ENABLED", "false")),
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip(),
        kafka_turn_topic=os.getenv("KAFKA_TURN_TOPIC", "msgbot.turns").strip() or "msgbot.turns",
        kafka_turn_consumer_group=os.getenv("KAFKA_TURN_CONSUMER_GROUP", "msgbot-turn-workers").strip() or "msgbot-turn-workers",
        kafka_poll_timeout_ms=int(os.getenv("KAFKA_POLL_TIMEOUT_MS", "1000")),
        kafka_notification_topic=os.getenv("KAFKA_NOTIFICATION_TOPIC", "msgbot.notifications").strip() or "msgbot.notifications",
        kafka_notification_consumer_group=os.getenv("KAFKA_NOTIFICATION_CONSUMER_GROUP", "msgbot-notification-workers").strip() or "msgbot-notification-workers",
        admin_api_key=os.getenv("ADMIN_API_KEY", "").strip(),
        admin_api_rate_limit_per_minute=int(os.getenv("ADMIN_API_RATE_LIMIT_PER_MINUTE", "60")),
        admin_auth_token_ttl_minutes=int(os.getenv("ADMIN_AUTH_TOKEN_TTL_MINUTES", "480")),
        session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "10")),
        max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "1500")),
        automation_enabled=_as_bool(os.getenv("AUTOMATION_ENABLED", "true")),
        doctor_reminder_enabled=_as_bool(os.getenv("DOCTOR_REMINDER_ENABLED", "true")),
        doctor_reminder_interval_seconds=int(os.getenv("DOCTOR_REMINDER_INTERVAL_SECONDS", "60")),
        doctor_reminder_lead_minutes=int(os.getenv("DOCTOR_REMINDER_LEAD_MINUTES", "10")),
        doctor_reminder_window_seconds=int(os.getenv("DOCTOR_REMINDER_WINDOW_SECONDS", "30")),
    )
