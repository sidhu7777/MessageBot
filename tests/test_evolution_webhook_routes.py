from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.evolution_webhook_routes import register_evolution_webhook_routes
from src.repositories.evolution_repository import EvolutionDoctorContext


class _FakeRepo:
    def resolve_doctor_context(self, *, instance_name: str = "", connected_account: str = ""):
        if instance_name != "doc7-instance":
            return None
        return EvolutionDoctorContext(
            doctor_id=7,
            admin_id=4,
            clinic_id=11,
            instance_name="doc7-instance",
            account_identity="919876543210",
            doctor_name="Dr. Sanjay",
            clinic_name="Sanjay Clinic",
            slug="Dr.SanjayVinayak",
        )


class _FakePolicy:
    def __init__(self) -> None:
        self._count = 0

    def register_inbound(self, *, doctor_id: int, patient_identity: str) -> int:
        self._count += 1
        return self._count


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_text(self, *, instance_name: str, remote_jid: str, text: str) -> None:
        self.sent.append(
            {
                "instance_name": instance_name,
                "remote_jid": remote_jid,
                "text": text,
            }
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        evolution_webhook_url="/evolution/webhook",
        evolution_webhook_secret="",
        evolution_booking_base_url="https://daptoservices.vinfocom.co.in",
        evolution_booking_path_prefix="/whatsapp/web",
        evolution_welcome_template="Welcome to {clinic_name}. Doctor: {doctor_name}. Book here: {booking_link}",
        evolution_warning_text="Please use the booking link and do not spam messages.",
    )


def _payload(text: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "doc7-instance",
        "data": {
            "key": {
                "remoteJid": "919000000001@s.whatsapp.net",
                "fromMe": False,
            },
            "message": {
                "conversation": text,
            },
        },
    }


def test_evolution_webhook_welcome_warning_then_silence() -> None:
    app = FastAPI()
    client = _FakeClient()
    register_evolution_webhook_routes(
        app,
        settings=_settings(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        evolution_repository=_FakeRepo(),
        evolution_policy=_FakePolicy(),
        evolution_api_client=client,
    )
    test_client = TestClient(app)

    r1 = test_client.post("/evolution/webhook", json=_payload("hi"))
    r2 = test_client.post("/evolution/webhook", json=_payload("hello"))
    r3 = test_client.post("/evolution/webhook", json=_payload("again"))

    assert r1.status_code == 200
    assert r1.json()["status"] == "welcome_sent"
    assert r2.status_code == 200
    assert r2.json()["status"] == "warning_sent"
    assert r3.status_code == 200
    assert r3.json()["status"] == "silenced"

    assert len(client.sent) == 2
    assert "Dr. Sanjay" in client.sent[0]["text"]
    assert "https://daptoservices.vinfocom.co.in/whatsapp/web/Dr.SanjayVinayak" in client.sent[0]["text"]
    assert "do not spam" in client.sent[1]["text"].lower()


def test_evolution_webhook_event_suffix_route_is_supported() -> None:
    app = FastAPI()
    client = _FakeClient()
    register_evolution_webhook_routes(
        app,
        settings=_settings(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        evolution_repository=_FakeRepo(),
        evolution_policy=_FakePolicy(),
        evolution_api_client=client,
    )
    test_client = TestClient(app)

    response = test_client.post("/evolution/webhook/messages-upsert", json={"instance": "doc7-instance", "data": _payload("hi")["data"]})

    assert response.status_code == 200
    assert response.json()["status"] == "welcome_sent"
    assert len(client.sent) == 1
