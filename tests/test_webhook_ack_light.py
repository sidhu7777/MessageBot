import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main


def test_whatsapp_webhook_ack_first_avoids_full_session_load(monkeypatch) -> None:
    calls = {"session": 0, "cached_state": 0, "send": 0}

    class _ConversationRepoFail:
        def seen_or_add_message_sid(self, message_sid: str, user_id: str, body: str) -> bool:
            raise AssertionError("DB dedup should not be called on webhook ACK path")

    monkeypatch.setattr(app_main, "conversation_repository", _ConversationRepoFail(), raising=True)
    monkeypatch.setattr(app_main.turn_processor, "submit", lambda task: True, raising=True)

    original_get = app_main.session_manager.get_or_create

    def _wrapped_get(user_id: str):
        calls["session"] += 1
        return original_get(user_id)

    original_cached_state = app_main.session_manager.get_cached_state

    def _wrapped_cached_state(user_id: str, default: str = "INIT"):
        calls["cached_state"] += 1
        return original_cached_state(user_id, default)

    monkeypatch.setattr(app_main.session_manager, "get_or_create", _wrapped_get, raising=True)
    monkeypatch.setattr(app_main.session_manager, "get_cached_state", _wrapped_cached_state, raising=True)

    def _send_stub(*args, **kwargs):
        calls["send"] += 1

    monkeypatch.setattr(app_main, "_send_plain_channel_message", _send_stub, raising=True)

    with TestClient(app_main.app) as client:
        resp = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+919000000111",
                "Body": "hello",
                "MessageSid": f"SM_ACK_LIGHT_{int(time.time() * 1000)}",
            },
        )
    assert resp.status_code == 200
    assert calls["session"] == 0
    assert calls["cached_state"] == 1
    assert calls["send"] == 0


def test_telegram_webhook_ack_first_avoids_full_session_load(monkeypatch) -> None:
    calls = {"session": 0, "cached_state": 0, "send": 0}

    monkeypatch.setattr(app_main.turn_processor, "submit", lambda task: True, raising=True)

    original_get = app_main.session_manager.get_or_create

    def _wrapped_get(user_id: str):
        calls["session"] += 1
        return original_get(user_id)

    original_cached_state = app_main.session_manager.get_cached_state

    def _wrapped_cached_state(user_id: str, default: str = "INIT"):
        calls["cached_state"] += 1
        return original_cached_state(user_id, default)

    monkeypatch.setattr(app_main.session_manager, "get_or_create", _wrapped_get, raising=True)
    monkeypatch.setattr(app_main.session_manager, "get_cached_state", _wrapped_cached_state, raising=True)

    def _send_stub(*args, **kwargs):
        calls["send"] += 1

    monkeypatch.setattr(app_main, "_send_plain_channel_message", _send_stub, raising=True)

    payload = {
        "message": {
            "message_id": int(time.time() * 1000) % 1000000000,
            "text": "Hi",
            "from": {"id": 123456789},
            "chat": {"id": 123456789, "type": "private"},
        }
    }
    with TestClient(app_main.app) as client:
        resp = client.post("/telegram/webhook", json=payload)
    assert resp.status_code == 200
    assert calls["session"] == 0
    assert calls["cached_state"] == 1
    assert calls["send"] == 0
