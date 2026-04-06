import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import register_webhook_routes
from src.runtime.channel_delivery import ChannelDelivery


class _DummyLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple] = []

    def info(self, *_a, **_k):
        return None

    def warning(self, *a, **_k):
        self.warnings.append((a, _k))
        return None

    def error(self, *_a, **_k):
        return None

    def exception(self, *_a, **_k):
        return None


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


class _BufferResult:
    pending_count = 1
    collapsed = False
    dropped_oldest = False


class _DummyBuffer:
    def push(self, _task):
        return _BufferResult()

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


class _DummyRouteCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value: str, ex=None):
        self._store[key] = value
        return True

    def incr(self, key: str):
        current = int(str(self._store.get(key) or "0"))
        current += 1
        self._store[key] = str(current)
        return current


def _settings(*, strict: bool = True, telegram_secret: str = "", admin_api_key: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        twilio_webhook_url="/webhook",
        telegram_webhook_url="/telegram/webhook",
        whatsapp_webhook_url="/whatsapp/webhook",
        infobip_webhook_url="/infobip/webhook",
        channel_routing_strict=strict,
        twilio_use_rest_responses=True,
        enable_twilio_signature_validation=False,
        enable_meta_signature_validation=False,
        meta_app_secret="",
        whatsapp_webhook_verify_token="verify",
        telegram_webhook_secret=telegram_secret,
        whatsapp_provider="auto",
        whatsapp_api_token="",
        whatsapp_phone_number_id="",
        infobip_api_key="",
        infobip_base_url="",
        infobip_whatsapp_number="",
        admin_api_key=admin_api_key,
    )


def _tg_payload(msg_id: int = 1234, user_id: int = 10001, text: str = "hello") -> dict:
    return {
        "message": {
            "message_id": msg_id,
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": user_id},
        }
    }


def _build_app(
    *,
    strict: bool = True,
    telegram_secret: str = "",
    route_cache=None,
    admin_api_key: str = "",
):
    app = FastAPI()
    logger = _DummyLogger()
    turn_processor = _DummyTurnProcessor()
    route_calls = []
    bot_calls = []
    register_webhook_routes(
        app,
        settings=_settings(strict=strict, telegram_secret=telegram_secret, admin_api_key=admin_api_key),
        logger=logger,
        request_validator=None,
        sid_store=_DummySidStore(),
        session_manager=_DummySessionManager(),
        twilio_client=None,
        turn_processor=turn_processor,
        booking_repository=None,
        channel_account_repository=_DummyChannelRepo(),
        user_processing_guard=_DummyGuard(),
        user_turn_buffer=_DummyBuffer(),
        set_user_bot_identity=lambda uid, identity: bot_calls.append((uid, identity)),
        set_user_route_context=lambda uid, ctx: route_calls.append((uid, dict(ctx))),
        submit_next_buffered_turn=lambda _uid: None,
        get_telegram_bot_username=lambda: "runtime_bot",
        route_cache_client=route_cache,
    )
    return app, logger, turn_processor, route_calls, bot_calls


def test_telegram_keyed_valid_scoped_enqueue() -> None:
    app, _logger, turn_processor, route_calls, bot_calls = _build_app(strict=True)
    client = TestClient(app)
    resp = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=11),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    assert resp.status_code == 200
    assert len(turn_processor.tasks) == 1
    assert turn_processor.tasks[0].from_number == "acct:42|telegram:10001"
    assert route_calls[0][1]["doctor_id"] == 77
    assert bot_calls[0][1] == "telegram_username:doctor_bot"


def test_telegram_keyed_invalid_secret_is_dropped() -> None:
    app, _logger, turn_processor, _route_calls, _bot_calls = _build_app(strict=True)
    client = TestClient(app)
    resp = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=12),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 200
    assert len(turn_processor.tasks) == 0


def test_telegram_keyed_unknown_key_is_dropped() -> None:
    app, _logger, turn_processor, _route_calls, _bot_calls = _build_app(strict=True)
    client = TestClient(app)
    resp = client.post(
        "/telegram/webhook/unknown",
        json=_tg_payload(msg_id=13),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    assert resp.status_code == 200
    assert len(turn_processor.tasks) == 0


def test_telegram_duplicate_sid_is_deduplicated() -> None:
    app, _logger, turn_processor, _route_calls, _bot_calls = _build_app(strict=True)
    client = TestClient(app)
    first = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=99, text="first"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    second = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=99, text="duplicate"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(turn_processor.tasks) == 1


def test_telegram_missing_key_rejected_in_strict_mode() -> None:
    app, _logger, turn_processor, _route_calls, _bot_calls = _build_app(strict=True)
    client = TestClient(app)
    resp = client.post("/telegram/webhook", json=_tg_payload(msg_id=21))
    assert resp.status_code == 200
    assert len(turn_processor.tasks) == 0


def test_telegram_missing_key_allowed_in_non_strict_with_global_secret() -> None:
    app, _logger, turn_processor, _route_calls, bot_calls = _build_app(strict=False, telegram_secret="global-secret")
    client = TestClient(app)
    resp = client.post(
        "/telegram/webhook",
        json=_tg_payload(msg_id=22),
        headers={"X-Telegram-Bot-Api-Secret-Token": "global-secret"},
    )
    assert resp.status_code == 200
    assert len(turn_processor.tasks) == 1
    assert turn_processor.tasks[0].from_number == "telegram:10001"
    assert bot_calls[0][1] == "telegram_username:runtime_bot"


def test_telegram_route_resolution_uses_cache_after_first_call() -> None:
    cache = _DummyRouteCache()
    app = FastAPI()
    settings = _settings(strict=True)
    sid_store = _DummySidStore()
    session_manager = _DummySessionManager()
    turn_processor = _DummyTurnProcessor()
    guard = _DummyGuard()
    buffer = _DummyBuffer()
    route_calls = []
    bot_calls = []

    class _CountingRepo(_DummyChannelRepo):
        def __init__(self):
            super().__init__()
            self.key_calls = 0

        def resolve_by_webhook_key(self, *, channel: str, webhook_key: str, webhook_secret: str = ""):
            self.key_calls += 1
            return super().resolve_by_webhook_key(
                channel=channel,
                webhook_key=webhook_key,
                webhook_secret=webhook_secret,
            )

    repo = _CountingRepo()
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
        channel_account_repository=repo,
        user_processing_guard=guard,
        user_turn_buffer=buffer,
        set_user_bot_identity=lambda uid, identity: bot_calls.append((uid, identity)),
        set_user_route_context=lambda uid, ctx: route_calls.append((uid, dict(ctx))),
        submit_next_buffered_turn=lambda _uid: None,
        get_telegram_bot_username=lambda: "runtime_fallback_bot",
        route_cache_client=cache,
    )
    client = TestClient(app)
    first = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=401),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    second = client.post(
        "/telegram/webhook/doc42",
        json=_tg_payload(msg_id=402),
        headers={"X-Telegram-Bot-Api-Secret-Token": "sec42"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(turn_processor.tasks) == 2
    assert repo.key_calls == 1


def test_route_cache_invalidation_hook_bumps_version() -> None:
    cache = _DummyRouteCache()
    app, _logger, _tp, _rc, _bc = _build_app(
        strict=True,
        route_cache=cache,
        admin_api_key="admin-token-1",
    )
    client = TestClient(app)
    r_forbidden = client.post("/internal/route-cache/invalidate")
    assert r_forbidden.status_code == 403
    r_ok = client.post(
        "/internal/route-cache/invalidate",
        headers={"X-Route-Cache-Token": "admin-token-1"},
    )
    assert r_ok.status_code == 200
    payload = r_ok.json()
    assert payload["ok"] is True
    assert int(payload["route_cache_version"]) >= 1


class _HTTPResp:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _delivery_settings() -> SimpleNamespace:
    return SimpleNamespace(
        whatsapp_provider="auto",
        whatsapp_api_token="",
        whatsapp_phone_number_id="",
        whatsapp_graph_api_version="v21.0",
        infobip_api_key="",
        infobip_base_url="",
        infobip_whatsapp_number="",
        twilio_whatsapp_from="whatsapp:+10000000000",
        twilio_status_callback_url="",
        twilio_send_retries=0,
        telegram_bot_token="",
    )


def test_delivery_meta_uses_account_credentials(monkeypatch) -> None:
    captured = {}

    def _fake_open(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _HTTPResp({"messages": [{"id": "wamid.meta.1"}]})

    monkeypatch.setattr("src.runtime.channel_delivery.urlrequest.urlopen", _fake_open)
    delivery = ChannelDelivery(
        settings=_delivery_settings(),
        twilio_client=None,
        logger=_DummyLogger(),
        log_event_fn=lambda *_a, **_k: None,
        extract_chat_id_fn=lambda x: x,
        channel_account_lookup_fn=lambda account_id: {
            "provider": "meta",
            "whatsapp_api_token": "meta-token-db",
            "whatsapp_phone_number_id": "555123",
        }
        if account_id == 42
        else {},
    )
    sid = delivery.send_plain_channel_message("acct:42|whatsapp:+919999999999", "hello")
    assert sid == "wamid.meta.1"
    assert "555123/messages" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer meta-token-db"


def test_delivery_infobip_uses_account_credentials(monkeypatch) -> None:
    captured = {}

    def _fake_open(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _HTTPResp({"messages": [{"messageId": "ib.1"}]})

    monkeypatch.setattr("src.runtime.channel_delivery.urlrequest.urlopen", _fake_open)
    delivery = ChannelDelivery(
        settings=_delivery_settings(),
        twilio_client=None,
        logger=_DummyLogger(),
        log_event_fn=lambda *_a, **_k: None,
        extract_chat_id_fn=lambda x: x,
        channel_account_lookup_fn=lambda account_id: {
            "provider": "infobip",
            "infobip_api_key": "ib-key-db",
            "infobip_base_url": "api.infobip.local",
            "infobip_whatsapp_number": "whatsapp:+14150001111",
        }
        if account_id == 42
        else {},
    )
    sid = delivery.send_plain_channel_message("acct:42|whatsapp:+919999999999", "hello")
    assert sid == "ib.1"
    assert captured["url"] == "https://api.infobip.local/whatsapp/1/message/text"
    assert captured["headers"]["Authorization"] == "App ib-key-db"


def test_delivery_twilio_uses_account_client_and_sender(monkeypatch) -> None:
    create_calls = []

    class _FakeMessages:
        def create(self, **kwargs):
            create_calls.append(kwargs)
            return SimpleNamespace(sid="SM123")

    fake_client = SimpleNamespace(messages=_FakeMessages())

    delivery = ChannelDelivery(
        settings=_delivery_settings(),
        twilio_client=None,
        logger=_DummyLogger(),
        log_event_fn=lambda *_a, **_k: None,
        extract_chat_id_fn=lambda x: x,
        channel_account_lookup_fn=lambda account_id: {
            "provider": "twilio",
            "twilio_account_sid": "AC-db",
            "twilio_auth_token": "auth-db",
            "twilio_whatsapp_from": "whatsapp:+14156667777",
        }
        if account_id == 42
        else {},
    )
    monkeypatch.setattr(
        delivery,
        "_twilio_client_for_account",
        lambda account: fake_client if account.get("twilio_account_sid") == "AC-db" else None,
    )
    sid = delivery.send_plain_channel_message("acct:42|whatsapp:+919999999999", "hello")
    assert sid == "SM123"
    assert len(create_calls) == 1
    assert create_calls[0]["from_"] == "whatsapp:+14156667777"
    assert create_calls[0]["to"] == "whatsapp:+919999999999"
    assert create_calls[0]["body"] == "hello"
