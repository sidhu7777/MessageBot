import json
from types import SimpleNamespace

from src.runtime.channel_delivery import ChannelDelivery


class _DummyLogger:
    def info(self, *_a, **_k):
        return None

    def warning(self, *_a, **_k):
        return None

    def error(self, *_a, **_k):
        return None

    def exception(self, *_a, **_k):
        return None


class _DummyHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        whatsapp_provider="auto",
        whatsapp_api_token="",
        whatsapp_phone_number_id="",
        whatsapp_graph_api_version="v21.0",
        infobip_api_key="",
        infobip_base_url="",
        infobip_whatsapp_number="",
        twilio_whatsapp_from="",
        twilio_status_callback_url="",
        twilio_send_retries=0,
        telegram_bot_token="",
    )


def test_meta_send_uses_channel_account_credentials(monkeypatch) -> None:
    captured = {}

    def _fake_open(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data.decode("utf-8")
        return _DummyHTTPResponse({"messages": [{"id": "wamid.account.1"}]})

    monkeypatch.setattr("src.runtime.channel_delivery.urlrequest.urlopen", _fake_open)

    delivery = ChannelDelivery(
        settings=_settings(),
        twilio_client=None,
        logger=_DummyLogger(),
        log_event_fn=lambda *_a, **_k: None,
        extract_chat_id_fn=lambda uid: uid,
        channel_account_lookup_fn=lambda account_id: {
            "provider": "meta",
            "whatsapp_api_token": "token-from-db",
            "whatsapp_phone_number_id": "1234567890",
        }
        if account_id == 99
        else {},
    )
    sid = delivery.send_plain_channel_message("acct:99|whatsapp:+919876543210", "hello")
    assert sid == "wamid.account.1"
    assert "1234567890/messages" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer token-from-db"

