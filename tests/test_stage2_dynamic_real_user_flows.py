import random
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import register_webhook_routes


class _Logger:
    def info(self, *_a, **_k):
        return None

    def warning(self, *_a, **_k):
        return None


class _SidStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def seen_or_add(self, sid: str) -> bool:
        if sid in self._seen:
            return True
        self._seen.add(sid)
        return False


class _SessionManager:
    def get_cached_state(self, _user_id: str, default: str = "INIT") -> str:
        return default


class _Guard:
    def acquire(self, _user_id: str) -> bool:
        return True

    def release(self, _user_id: str) -> None:
        return None


class _PushResult:
    pending_count = 1
    collapsed = False
    dropped_oldest = False


class _Buffer:
    def push(self, _task):
        return _PushResult()

    def record_dispatch(self, _user_id: str, _body: str) -> None:
        return None


class _TurnProcessor:
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, task) -> bool:
        self.tasks.append(task)
        return True

    def backlog_size(self) -> int:
        return len(self.tasks)


@dataclass
class _Account:
    channel_account_id: int
    sender_identity: str
    provider: str
    webhook_key: str
    webhook_secret: str
    doctor_id: int
    admin_id: int


class _ChannelRepo:
    def __init__(self, accounts: list[_Account]) -> None:
        self._by_key = {a.webhook_key: a for a in accounts}

    def resolve_by_webhook_key(self, *, channel: str, webhook_key: str, webhook_secret: str = ""):
        if channel != "telegram":
            return None
        account = self._by_key.get(webhook_key)
        if not account:
            return None
        if account.webhook_secret != webhook_secret:
            return None
        return SimpleNamespace(
            channel_account_id=account.channel_account_id,
            sender_identity=account.sender_identity,
            provider=account.provider,
        )

    def resolve_by_sender_identity(self, *, channel: str, sender_identity: str):
        return None

    def resolve_binding(self, channel_account_id: int):
        for account in self._by_key.values():
            if account.channel_account_id == channel_account_id:
                return {
                    "doctor_id": account.doctor_id,
                    "admin_id": account.admin_id,
                    "channel_account_id": account.channel_account_id,
                }
        return None


def _settings(*, strict: bool, telegram_secret: str = "") -> SimpleNamespace:
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
    )


def _payload(*, msg_id: int, user_id: int, text: str) -> dict:
    return {
        "message": {
            "message_id": msg_id,
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": user_id},
        }
    }


def _build_app(accounts: list[_Account], *, strict: bool, telegram_secret: str = ""):
    app = FastAPI()
    tp = _TurnProcessor()
    route_calls = []
    bot_calls = []
    register_webhook_routes(
        app,
        settings=_settings(strict=strict, telegram_secret=telegram_secret),
        logger=_Logger(),
        request_validator=None,
        sid_store=_SidStore(),
        session_manager=_SessionManager(),
        twilio_client=None,
        turn_processor=tp,
        booking_repository=None,
        channel_account_repository=_ChannelRepo(accounts),
        user_processing_guard=_Guard(),
        user_turn_buffer=_Buffer(),
        set_user_bot_identity=lambda uid, ident: bot_calls.append((uid, ident)),
        set_user_route_context=lambda uid, ctx: route_calls.append((uid, dict(ctx))),
        submit_next_buffered_turn=lambda _uid: None,
        get_telegram_bot_username=lambda: "legacy_runtime_bot",
    )
    return app, tp, route_calls, bot_calls


def _random_accounts(rng: random.Random, count: int = 4) -> list[_Account]:
    accounts: list[_Account] = []
    for i in range(count):
        account_id = 100 + i
        accounts.append(
            _Account(
                channel_account_id=account_id,
                sender_identity=f"doctor_bot_{account_id}",
                provider="telegram",
                webhook_key=f"k_{rng.randint(1000, 9999)}_{i}",
                webhook_secret=f"s_{rng.randint(10000, 99999)}_{i}",
                doctor_id=700 + i,
                admin_id=50 + i,
            )
        )
    return accounts


def test_dynamic_real_user_keyed_routing_many_events() -> None:
    rng = random.Random(20260319)
    accounts = _random_accounts(rng, count=5)
    app, tp, route_calls, bot_calls = _build_app(accounts, strict=True)
    client = TestClient(app)

    expected = []
    for idx in range(60):
        account = rng.choice(accounts)
        user_id = rng.randint(100000, 999999)
        msg_id = 1000 + idx
        text = f"hello-{idx}-{rng.randint(1,999)}"
        resp = client.post(
            f"/telegram/webhook/{account.webhook_key}",
            json=_payload(msg_id=msg_id, user_id=user_id, text=text),
            headers={"X-Telegram-Bot-Api-Secret-Token": account.webhook_secret},
        )
        assert resp.status_code == 200
        expected.append((account, user_id))

    assert len(tp.tasks) == len(expected)
    assert len(route_calls) == len(expected)
    assert len(bot_calls) == len(expected)
    for i, task in enumerate(tp.tasks):
        account, user_id = expected[i]
        assert task.from_number == f"acct:{account.channel_account_id}|telegram:{user_id}"
        assert route_calls[i][1]["doctor_id"] == account.doctor_id
        assert route_calls[i][1]["admin_id"] == account.admin_id
        assert bot_calls[i][1] == f"telegram_username:{account.sender_identity}"


def test_dynamic_invalid_traffic_is_rejected_in_strict_mode() -> None:
    rng = random.Random(20260320)
    accounts = _random_accounts(rng, count=4)
    app, tp, _route_calls, _bot_calls = _build_app(accounts, strict=True)
    client = TestClient(app)

    accepted = 0
    for idx in range(80):
        account = rng.choice(accounts)
        user_id = rng.randint(1000, 9000)
        msg_id = 2000 + idx
        mode = rng.choice(["valid", "bad_secret", "bad_key"])
        key = account.webhook_key
        secret = account.webhook_secret
        if mode == "bad_secret":
            secret = "wrong-secret"
        elif mode == "bad_key":
            key = f"{account.webhook_key}_bad"
        resp = client.post(
            f"/telegram/webhook/{key}",
            json=_payload(msg_id=msg_id, user_id=user_id, text=f"m-{idx}"),
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
        assert resp.status_code == 200
        if mode == "valid":
            accepted += 1

    assert len(tp.tasks) == accepted


def test_dynamic_dedup_is_scoped_by_account_and_user() -> None:
    rng = random.Random(20260321)
    accounts = _random_accounts(rng, count=2)
    app, tp, _route_calls, _bot_calls = _build_app(accounts, strict=True)
    client = TestClient(app)

    a0, a1 = accounts[0], accounts[1]
    user_id = 555001
    msg_id = 333

    r1 = client.post(
        f"/telegram/webhook/{a0.webhook_key}",
        json=_payload(msg_id=msg_id, user_id=user_id, text="one"),
        headers={"X-Telegram-Bot-Api-Secret-Token": a0.webhook_secret},
    )
    r2 = client.post(
        f"/telegram/webhook/{a0.webhook_key}",
        json=_payload(msg_id=msg_id, user_id=user_id, text="dup-same-account"),
        headers={"X-Telegram-Bot-Api-Secret-Token": a0.webhook_secret},
    )
    # Same user/message_id but different channel account should be accepted.
    r3 = client.post(
        f"/telegram/webhook/{a1.webhook_key}",
        json=_payload(msg_id=msg_id, user_id=user_id, text="same-msg-other-account"),
        headers={"X-Telegram-Bot-Api-Secret-Token": a1.webhook_secret},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert len(tp.tasks) == 2
    assert tp.tasks[0].from_number == f"acct:{a0.channel_account_id}|telegram:{user_id}"
    assert tp.tasks[1].from_number == f"acct:{a1.channel_account_id}|telegram:{user_id}"


def test_dynamic_non_strict_legacy_path_accepts_real_user_flow() -> None:
    rng = random.Random(20260322)
    accounts = _random_accounts(rng, count=3)
    app, tp, _route_calls, bot_calls = _build_app(
        accounts,
        strict=False,
        telegram_secret="legacy-global-secret",
    )
    client = TestClient(app)

    for idx in range(25):
        user_id = 880000 + idx
        resp = client.post(
            "/telegram/webhook",
            json=_payload(msg_id=9000 + idx, user_id=user_id, text=f"legacy-{idx}"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "legacy-global-secret"},
        )
        assert resp.status_code == 200

    assert len(tp.tasks) == 25
    for idx, task in enumerate(tp.tasks):
        assert task.from_number == f"telegram:{880000 + idx}"
    # Legacy path uses runtime username fallback.
    assert bot_calls
    assert bot_calls[0][1] == "telegram_username:legacy_runtime_bot"

