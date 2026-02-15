from dataclasses import dataclass
import os


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    enable_response_polish: bool = False
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

    session_ttl_minutes: int = 120
    max_message_chars: int = 1500



def load_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "whatsapp-appointment-bot"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
        llm_model=os.getenv("LLM_MODEL", "qwen3:0.6b"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "33")),
        ollama_auto_start=_as_bool(os.getenv("OLLAMA_AUTO_START", "true")),
        ollama_auto_pull=_as_bool(os.getenv("OLLAMA_AUTO_PULL", "true")),
        ollama_startup_timeout_seconds=float(
            os.getenv("OLLAMA_STARTUP_TIMEOUT_SECONDS", "30")
        ),
        mixed_response_language=os.getenv("MIXED_RESPONSE_LANGUAGE", "auto"),
        enable_llm_polish=_as_bool(os.getenv("ENABLE_LLM_POLISH", "true")),
        enable_response_polish=_as_bool(os.getenv("ENABLE_RESPONSE_POLISH", "false")),
        enable_db_booking=_as_bool(os.getenv("ENABLE_DB_BOOKING", "true")),
        enable_twilio_signature_validation=_as_bool(
            os.getenv("ENABLE_TWILIO_SIGNATURE_VALIDATION", "false")
        ),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_whatsapp_from=os.getenv("TWILIO_WHATSAPP_FROM", ""),
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
        session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "120")),
        max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "1500")),
    )
