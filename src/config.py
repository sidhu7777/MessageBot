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
    enable_db_booking: bool = True

    enable_twilio_signature_validation: bool = False
    twilio_auth_token: str = ""

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
        enable_db_booking=_as_bool(os.getenv("ENABLE_DB_BOOKING", "true")),
        enable_twilio_signature_validation=_as_bool(
            os.getenv("ENABLE_TWILIO_SIGNATURE_VALIDATION", "false")
        ),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", "120")),
        max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "1500")),
    )
