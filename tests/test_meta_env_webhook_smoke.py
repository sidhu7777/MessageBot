import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import register_webhook_routes
from src.config import load_settings


@dataclass
class _PushResult:
    pending_count: int = 1
    collapsed: bool = False
    dropped_oldest: bool = False


class _SidStore:
    def seen_or_add(self, _sid: str) -> bool:
        return False


class _Session:
    state = "INIT"


class _SessionManager:
    def get_or_create(self, _user: str):
        return _Session()


class _TurnProcessor:
    def submit(self, _task) -> bool:
        return True

    def backlog_size(self) -> int:
        return 0


class _Guard:
    def acquire(self, _user: str) -> bool:
        return True

    def release(self, _user: str) -> None:
        return None


class _Buffer:
    def push(self, _task) -> _PushResult:
        return _PushResult()

    def record_dispatch(self, _user: str, _body: str) -> None:
        return None


class _Validator:
    def validate(self, *_args, **_kwargs) -> bool:
        return True


def _required_meta_env_errors() -> list[str]:
    errors: list[str] = []
    token = os.getenv("WHATSAPP_BOT_TOKEN", "").strip()
    if not token:
        errors.append("Set WHATSAPP_BOT_TOKEN.")
    if not os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip():
        errors.append("Set WHATSAPP_PHONE_NUMBER_ID.")
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    if not verify_token:
        errors.append("Set WHATSAPP_VERIFY_TOKEN.")
    if not os.getenv("WHATSAPP_WEBHOOK_URL", "").strip():
        errors.append("Set WHATSAPP_WEBHOOK_URL.")
    return errors


def test_meta_env_required_values_present() -> None:
    load_dotenv(override=False)
    errors = _required_meta_env_errors()
    assert not errors, "Meta WhatsApp env incomplete:\n- " + "\n- ".join(errors)


def test_meta_webhook_verify_endpoint_uses_env_values() -> None:
    load_dotenv(override=False)
    settings = load_settings()
    verify_token = (settings.whatsapp_webhook_verify_token or "").strip()
    assert verify_token, "Missing verify token env value."

    whatsapp_url = (settings.whatsapp_webhook_url or "").strip()
    path = urlparse(whatsapp_url).path if whatsapp_url else "/whatsapp/webhook"
    path = (path or "/whatsapp/webhook").rstrip("/") or "/whatsapp/webhook"

    app = FastAPI()
    register_webhook_routes(
        app,
        settings=settings,
        logger=type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})(),
        request_validator=_Validator(),
        sid_store=_SidStore(),
        session_manager=_SessionManager(),
        twilio_client=None,
        turn_processor=_TurnProcessor(),
        booking_repository=None,
        user_processing_guard=_Guard(),
        user_turn_buffer=_Buffer(),
        set_user_bot_identity=lambda *_args, **_kwargs: None,
        submit_next_buffered_turn=lambda *_args, **_kwargs: None,
        get_telegram_bot_username=lambda: "",
    )
    client = TestClient(app)
    challenge = "abc123challenge"
    resp = client.get(
        path,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token,
            "hub.challenge": challenge,
        },
    )
    assert resp.status_code == 200
    assert resp.text == challenge
