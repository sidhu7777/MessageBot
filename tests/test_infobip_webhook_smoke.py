from dataclasses import dataclass
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, task) -> bool:
        self.tasks.append(task)
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


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None


class _Validator:
    def validate(self, *_args, **_kwargs) -> bool:
        return True


def test_infobip_webhook_route_queues_turn() -> None:
    settings = load_settings()
    object.__setattr__(settings, "whatsapp_provider", "infobip")
    object.__setattr__(settings, "infobip_api_key", "dummy-key")
    object.__setattr__(settings, "infobip_base_url", "https://example.infobip.com")
    object.__setattr__(settings, "infobip_whatsapp_number", "+447860088970")
    object.__setattr__(settings, "infobip_webhook_url", "https://example.com/infobip/webhook")
    object.__setattr__(settings, "twilio_use_rest_responses", True)

    processor = _TurnProcessor()
    app = FastAPI()
    register_webhook_routes(
        app,
        settings=settings,
        logger=_Logger(),
        request_validator=_Validator(),
        sid_store=_SidStore(),
        session_manager=_SessionManager(),
        twilio_client=None,
        turn_processor=processor,
        booking_repository=None,
        user_processing_guard=_Guard(),
        user_turn_buffer=_Buffer(),
        set_user_bot_identity=lambda *_args, **_kwargs: None,
        submit_next_buffered_turn=lambda *_args, **_kwargs: None,
        get_telegram_bot_username=lambda: "",
    )
    client = TestClient(app)
    payload = {
        "results": [
            {
                "from": "919392569600",
                "to": "447860088970",
                "messageId": "ib-msg-1",
                "message": {"text": "hello infobip"},
            }
        ]
    }
    resp = client.post("/infobip/webhook", json=payload)
    assert resp.status_code == 200
    assert len(processor.tasks) == 1
    task = processor.tasks[0]
    assert task.from_number == "whatsapp:+919392569600"
    assert task.body == "hello infobip"
    assert task.inbound_sid == "ib-msg-1"
