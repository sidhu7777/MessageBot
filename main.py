import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
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
    booking_repository=BookingRepository.from_env() if settings.enable_db_booking else None,
    ttl_minutes=settings.session_ttl_minutes,
)
request_validator = RequestValidator(settings.twilio_auth_token)

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
    body = (form.get("Body") or "").strip()
    from_number = form.get("From") or "unknown"

    if settings.enable_twilio_signature_validation:
        signature = request.headers.get("X-Twilio-Signature", "")
        valid = request_validator.validate(str(request.url), dict(form), signature)
        if not valid:
            LOGGER.warning("Rejected webhook due to invalid Twilio signature")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    LOGGER.info("Incoming WhatsApp message from %s: %s", from_number, body)

    fsm = session_manager.get_or_create(from_number)
    reply = fsm.handle(body)
    reply = reply[: settings.max_message_chars]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    LOGGER.info("Reply generated in %dms (chars=%d)", elapsed_ms, len(reply))

    twiml = MessagingResponse()
    twiml.message(reply)
    return PlainTextResponse(str(twiml), media_type="application/xml")
