from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import register_webhook_routes


class _DummySidStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def seen_or_add(self, sid: str) -> bool:
        if sid in self._seen:
            return True
        self._seen.add(sid)
        return False


class _DummySessionManager:
    def get_cached_state(self, _user_id: str, default: str = "INIT") -> str:
        return default


class _DummyGuard:
    def acquire(self, _user_id: str) -> bool:
        return True

    def release(self, _user_id: str) -> None:
        return None


class _BufferedResult:
    pending_count = 1
    collapsed = False
    dropped_oldest = False


class _DummyBuffer:
    def push(self, _task):
        return _BufferedResult()

    def record_dispatch(self, _user_id: str, _body: str) -> None:
        return None


class _DummyTurnProcessor:
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, task) -> bool:
        self.tasks.append(task)
        return True

    def backlog_size(self) -> int:
        return len(self.tasks)


class _DummyChannelAccount:
    def __init__(self, channel_account_id: int, sender_identity: str, provider: str = "telegram") -> None:
        self.channel_account_id = channel_account_id
        self.sender_identity = sender_identity
        self.provider = provider


class _DummyChannelRepo:
    def __init__(self) -> None:
        self.account = _DummyChannelAccount(channel_account_id=42, sender_identity="doctor_bot")
        self.binding = {"doctor_id": 77, "admin_id": 5, "channel_account_id": 42}

    def resolve_by_webhook_key(self, *, channel: str, webhook_key: str, webhook_secret: str = ""):
        if channel == "telegram" and webhook_key == "doc42" and webhook_secret == "sec42":
            return self.account
        return None

    def resolve_by_sender_identity(self, *, channel: str, sender_identity: str):
        return None

    def resolve_binding(self, channel_account_id: int):
        if channel_account_id == 42:
            return dict(self.binding)
        return None


def _build_settings() -> SimpleNamespace:
    return SimpleNamespace(
        twilio_webhook_url="/webhook",
        telegram_webhook_url="/telegram/webhook",
        whatsapp_webhook_url="/whatsapp/webhook",
        infobip_webhook_url="/infobip/webhook",
        channel_routing_strict=True,
        twilio_use_rest_responses=True,
        enable_twilio_signature_validation=False,
        enable_meta_signature_validation=False,
        meta_app_secret="",
        whatsapp_webhook_verify_token="verify",
        telegram_webhook_secret="",
        whatsapp_provider="auto",
        whatsapp_api_token="",
        whatsapp_phone_number_id="",
        infobip_api_key="",
        infobip_base_url="",
        infobip_whatsapp_number="",
    )


def _payload() -> dict:
    return {
        "message": {
            "message_id": 1234,
            "text": "hello",
            "from": {"id": 10001},
            "chat": {"id": 10001},
        }
    }


def test_telegram_keyed_webhook_scopes_user_and_sets_context() -> None:
    app = FastAPI()
    settings = _build_settings()
    sid_store = _DummySidStore()
    session_manager = _DummySessionManager()
    turn_processor = _DummyTurnProcessor()
    guard = _DummyGuard()
    buffer = _DummyBuffer()
    route_context_calls = []
    bot_identity_calls = []

    register_webhook_routes(
        app,
        settings=settings,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        request_validator=None,
        sid_store=sid_store,
        session_manager=session_manager,
        twilio_client=None,
        turn_processor=turn_processor,
        booking_repository=None,
        channel_account_repository=_DummyChannelRepo(),
        user_processing_guard=guard,
        user_turn_buffer=buffer,
        set_user_bot_identity=lambda uid, identity: bot_identity_calls.append((uid, identity)),
        set_user_route_context=lambda uid, ctx: route_context_calls.append((uid, dict(ctx))),
        submit_next_buffered_turn=lambda _uid: None,
        get_telegram_bot_username=lambda: "runtime_fallback_bot",
    )
    client = TestClient(app)
    response = client.post(
        "/telegram/webhook/doc42",
        json=_payload(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    assert response.status_code == 200
    assert len(turn_processor.tasks) == 1
    task = turn_processor.tasks[0]
    assert task.from_number == "acct:42|telegram:10001"
    assert route_context_calls
    assert route_context_calls[0][0] == "acct:42|telegram:10001"
    assert route_context_calls[0][1]["doctor_id"] == 77
    assert bot_identity_calls
    assert bot_identity_calls[0][0] == "acct:42|telegram:10001"
    assert bot_identity_calls[0][1] == "telegram_username:doctor_bot"


def test_telegram_without_key_is_rejected_in_strict_mode() -> None:
    app = FastAPI()
    turn_processor = _DummyTurnProcessor()
    register_webhook_routes(
        app,
        settings=_build_settings(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        request_validator=None,
        sid_store=_DummySidStore(),
        session_manager=_DummySessionManager(),
        twilio_client=None,
        turn_processor=turn_processor,
        booking_repository=None,
        channel_account_repository=_DummyChannelRepo(),
        user_processing_guard=_DummyGuard(),
        user_turn_buffer=_DummyBuffer(),
        set_user_bot_identity=lambda *_a, **_k: None,
        set_user_route_context=lambda *_a, **_k: None,
        submit_next_buffered_turn=lambda _uid: None,
        get_telegram_bot_username=lambda: "runtime_fallback_bot",
    )
    client = TestClient(app)
    response = client.post("/telegram/webhook", json=_payload())
    assert response.status_code == 200
    assert len(turn_processor.tasks) == 0

