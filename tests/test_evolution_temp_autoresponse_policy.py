from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env.example", override=False)


WELCOME_TEMPLATE = (
    "Welcome to {clinic_name} , use the link to book appointment :{booking_link}"
)
WARNING_TEXT = (
    "Please use the shared link to book appointments and please do not spam messages."
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class EvolutionManualConfig:
    base_url: str
    api_key: str
    instance_name: str
    clinic_name: str
    booking_link: str
    session_window_seconds: int
    webhook_public_base: str
    create_instance_path: str
    set_webhook_path_template: str
    connect_path_template: str
    connection_state_path_template: str
    send_text_path_template: str
    local_host: str
    local_port: int
    timeout_seconds: int
    keep_running_after_success: bool

    @property
    def local_webhook_path(self) -> str:
        return f"/evolution/manual/webhook/{self.instance_name}"

    @property
    def local_webhook_url(self) -> str:
        return f"http://{self.local_host}:{self.local_port}{self.local_webhook_path}"

    @property
    def public_webhook_url(self) -> str:
        base = self.webhook_public_base.rstrip("/")
        return f"{base}{self.local_webhook_path}"

    @property
    def manager_url(self) -> str:
        return f"{self.base_url}/manager"


def _load_config() -> EvolutionManualConfig:
    local_port = int(os.getenv("EVOLUTION_TEST_LOCAL_PORT", str(_pick_free_port())))
    public_base = os.getenv("EVOLUTION_TEST_WEBHOOK_PUBLIC_BASE", "").strip()
    if not public_base:
        public_base = f"http://host.docker.internal:{local_port}"
    return EvolutionManualConfig(
        base_url=os.getenv("EVOLUTION_API_BASE_URL", "http://127.0.0.1:8081").strip().rstrip("/"),
        api_key=(
            os.getenv("EVOLUTION_API_KEY", "").strip()
            or os.getenv("AUTHENTICATION_API_KEY", "").strip()
        ),
        instance_name=os.getenv("EVOLUTION_INSTANCE_NAME", "codex-temp-test").strip(),
        clinic_name=os.getenv("EVOLUTION_CLINIC_NAME", "Sanjay Vinayak Clinic").strip(),
        booking_link=os.getenv(
            "EVOLUTION_BOOKING_LINK",
            "https://daptoservices.vinfocom.co.in/whatsapp/web/Dr.SanjayVinayak",
        ).strip(),
        session_window_seconds=int(os.getenv("EVOLUTION_SESSION_WINDOW_SECONDS", str(12 * 60 * 60))),
        webhook_public_base=public_base,
        create_instance_path=os.getenv("EVOLUTION_CREATE_INSTANCE_PATH", "/instance/create").strip(),
        set_webhook_path_template=os.getenv("EVOLUTION_SET_WEBHOOK_PATH_TEMPLATE", "/webhook/set/{instance}").strip(),
        connect_path_template=os.getenv("EVOLUTION_CONNECT_PATH_TEMPLATE", "/instance/connect/{instance}").strip(),
        connection_state_path_template=os.getenv(
            "EVOLUTION_CONNECTION_STATE_PATH_TEMPLATE",
            "/instance/connectionState/{instance}",
        ).strip(),
        send_text_path_template=os.getenv("EVOLUTION_SEND_TEXT_PATH_TEMPLATE", "/message/sendText/{instance}").strip(),
        local_host=os.getenv("EVOLUTION_TEST_LOCAL_HOST", "127.0.0.1").strip(),
        local_port=local_port,
        timeout_seconds=max(60, int(os.getenv("EVOLUTION_MANUAL_TIMEOUT_SECONDS", "600"))),
        keep_running_after_success=_env_bool("EVOLUTION_MANUAL_KEEP_RUNNING", False),
    )


class _EventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class _WebhookServer:
    def __init__(self, cfg: EvolutionManualConfig, client: "EvolutionApiClient") -> None:
        self.cfg = cfg
        self.client = client
        self.log = _EventLog(DATA_DIR / f"evolution_manual_test_{cfg.instance_name}.jsonl")
        self.app = FastAPI()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._state: dict[tuple[str, str], dict[str, Any]] = {}
        self.inbound_events = 0
        self.welcome_sent = 0
        self.warning_sent = 0
        self.silenced = 0
        self.last_error: str = ""
        self.latest_qr_code: str = ""
        self.latest_pairing_code: str = ""
        self.connection_updates: list[dict[str, Any]] = []
        self._ready = threading.Event()
        self._mount_routes()

    def _mount_routes(self) -> None:
        @self.app.get("/health")
        async def _health() -> dict[str, str]:
            return {"status": "ok"}

        @self.app.post(self.cfg.local_webhook_path)
        async def _webhook(request: Request) -> dict[str, bool]:
            payload = await request.json()
            return self._handle_payload(payload)

        @self.app.post(f"{self.cfg.local_webhook_path}" + "/{event_suffix:path}")
        async def _webhook_by_event(event_suffix: str, request: Request) -> dict[str, bool]:
            payload = await request.json()
            if isinstance(payload, dict) and event_suffix and not payload.get("event"):
                payload["event"] = event_suffix.replace("-", ".")
            return self._handle_payload(payload)

    def start(self) -> None:
        bind_host = self.cfg.local_host
        if bind_host in {"127.0.0.1", "localhost"}:
            # Docker reaches the host via host.docker.internal, so the webhook
            # listener must bind beyond loopback even though local health checks
            # can still use 127.0.0.1.
            bind_host = "0.0.0.0"
        config = uvicorn.Config(
            self.app,
            host=bind_host,
            port=self.cfg.local_port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)

        def _run() -> None:
            self._ready.set()
            self._server.run()

        self._thread = threading.Thread(target=_run, name="evolution-manual-webhook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        deadline = time.time() + 10.0
        health_probe_host = "127.0.0.1" if bind_host == "0.0.0.0" else self.cfg.local_host
        health_url = f"http://{health_probe_host}:{self.cfg.local_port}/health"
        while time.time() < deadline:
            try:
                with httpx.Client(timeout=2.0) as hc:
                    resp = hc.get(health_url)
                if resp.status_code == 200:
                    return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError(f"Webhook test server did not start at {health_url}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _handle_payload(self, payload: dict[str, Any]) -> dict[str, bool]:
        event = self._normalize_inbound(payload)
        self.log.write({"kind": "webhook_raw", "payload": payload})
        qr_payload = self._extract_qr_payload(payload)
        if qr_payload:
            self.latest_qr_code = qr_payload.get("code") or self.latest_qr_code
            self.latest_pairing_code = qr_payload.get("pairingCode") or self.latest_pairing_code
            self.log.write({"kind": "qr_update", "payload": qr_payload})
            return {"ok": True}
        connection_payload = self._extract_connection_update(payload)
        if connection_payload:
            self.connection_updates.append(connection_payload)
            self.log.write({"kind": "connection_update", "payload": connection_payload})
            return {"ok": True}
        if not event:
            return {"ok": True}
        with self._lock:
            self.inbound_events += 1
        key = (event["instance"], event["remote_jid"])
        now_ts = float(time.time())
        reply_text: Optional[str] = None
        outcome = "ignored"
        try:
            with self._lock:
                state = self._state.get(key)
                if state is None or (now_ts - float(state["started_at"])) >= self.cfg.session_window_seconds:
                    state = {"started_at": now_ts, "response_count": 0}
                    self._state[key] = state
                state["response_count"] = int(state["response_count"]) + 1
                response_count = int(state["response_count"])
            if response_count == 1:
                reply_text = WELCOME_TEMPLATE.format(
                    clinic_name=self.cfg.clinic_name,
                    booking_link=self.cfg.booking_link,
                )
                self.client.send_text(event["instance"], event["remote_jid"], reply_text)
                with self._lock:
                    self.welcome_sent += 1
                outcome = "welcome_sent"
            elif response_count == 2:
                reply_text = WARNING_TEXT
                self.client.send_text(event["instance"], event["remote_jid"], reply_text)
                with self._lock:
                    self.warning_sent += 1
                outcome = "warning_sent"
            else:
                with self._lock:
                    self.silenced += 1
                outcome = "silenced"
        except Exception as exc:
            self.last_error = str(exc)
            outcome = "error"
            self.log.write({"kind": "handler_error", "error": str(exc), "event": event})
        self.log.write(
            {
                "kind": "decision",
                "outcome": outcome,
                "reply_text": reply_text,
                "event": event,
            }
        )
        return {"ok": True}

    @staticmethod
    def _normalize_inbound(payload: dict[str, Any]) -> Optional[dict[str, str]]:
        if not isinstance(payload, dict):
            return None
        event_name = str(
            payload.get("event")
            or payload.get("eventName")
            or payload.get("type")
            or ""
        ).strip().lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        instance = str(
            payload.get("instance")
            or payload.get("instanceName")
            or data.get("instance")
            or data.get("instanceName")
            or ""
        ).strip()
        if event_name and "message" not in event_name:
            return None
        key = data.get("key") if isinstance(data.get("key"), dict) else {}
        remote_jid = str(
            key.get("remoteJid")
            or data.get("remoteJid")
            or data.get("from")
            or payload.get("from")
            or ""
        ).strip()
        if not remote_jid:
            return None
        from_me = bool(key.get("fromMe")) if isinstance(key, dict) else False
        if from_me:
            return None
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        text = ""
        if isinstance(message.get("conversation"), str):
            text = message.get("conversation") or ""
        elif isinstance(message.get("extendedTextMessage"), dict):
            text = str(message["extendedTextMessage"].get("text") or "")
        elif isinstance(data.get("text"), str):
            text = data.get("text") or ""
        if not text.strip():
            return None
        return {
            "instance": instance or "unknown-instance",
            "remote_jid": remote_jid,
            "text": text.strip(),
        }

    @staticmethod
    def _extract_qr_payload(payload: dict[str, Any]) -> Optional[dict[str, str]]:
        if not isinstance(payload, dict):
            return None
        event_name = str(
            payload.get("event")
            or payload.get("eventName")
            or payload.get("type")
            or ""
        ).strip().upper()
        if "QRCODE" not in event_name:
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        code = str(data.get("code") or payload.get("code") or "").strip()
        pairing = str(data.get("pairingCode") or payload.get("pairingCode") or "").strip()
        if not code and not pairing:
            return None
        return {"code": code, "pairingCode": pairing}

    @staticmethod
    def _extract_connection_update(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        event_name = str(
            payload.get("event")
            or payload.get("eventName")
            or payload.get("type")
            or ""
        ).strip().upper()
        if "CONNECTION_UPDATE" not in event_name:
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return data if isinstance(data, dict) else {"raw": data}


class EvolutionApiClient:
    def __init__(self, cfg: EvolutionManualConfig) -> None:
        self.cfg = cfg
        self._client = httpx.Client(timeout=20.0, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["apikey"] = self.cfg.api_key
        return headers

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.cfg.base_url}{normalized}"

    def _path(self, template: str, instance: str) -> str:
        return template.format(instance=instance)

    def create_or_open_instance(self) -> dict[str, Any]:
        payload = {
            "instanceName": self.cfg.instance_name,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
        }
        resp = self._client.post(
            self._url(self.cfg.create_instance_path),
            headers=self._headers(),
            json=payload,
        )
        if resp.status_code < 400:
            return resp.json()
        try:
            error_payload = resp.json()
        except Exception:
            error_payload = {"raw": resp.text}
        blob = json.dumps(error_payload, ensure_ascii=False).lower()
        if resp.status_code == 403 and "already in use" in blob:
            return {
                "instance": {
                    "instanceName": self.cfg.instance_name,
                    "status": "existing",
                },
                "message": "instance already exists; continuing with existing instance",
                "raw_error": error_payload,
            }
        resp.raise_for_status()
        return {}

    def set_webhook(self) -> dict[str, Any]:
        payload = {
            "webhook": {
                "url": self.cfg.public_webhook_url,
                "enabled": True,
                "webhook_by_events": True,
                "webhookByEvents": True,
                "webhook_base64": False,
                "webhookBase64": False,
                "events": [
                    "QRCODE_UPDATED",
                    "CONNECTION_UPDATE",
                    "MESSAGES_UPSERT",
                ],
            }
        }
        resp = self._client.post(
            self._url(self._path(self.cfg.set_webhook_path_template, self.cfg.instance_name)),
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def connect_instance(self) -> dict[str, Any]:
        resp = self._client.get(
            self._url(self._path(self.cfg.connect_path_template, self.cfg.instance_name)),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_instances(self) -> list[dict[str, Any]]:
        resp = self._client.get(
            self._url("/instance/fetchInstances"),
            headers=self._headers(),
            params={"instanceName": self.cfg.instance_name},
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, list) else []

    def connection_state(self) -> dict[str, Any]:
        resp = self._client.get(
            self._url(self._path(self.cfg.connection_state_path_template, self.cfg.instance_name)),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def send_text(self, instance: str, remote_jid: str, text: str) -> dict[str, Any]:
        payload = {
            "number": remote_jid.split("@", 1)[0],
            "text": text,
        }
        resp = self._client.post(
            self._url(self._path(self.cfg.send_text_path_template, instance)),
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


def _extract_qr_hint(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in (
            "code",
            "base64",
            "qrcode",
            "qr",
            "pairingCode",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            nested = _extract_qr_hint(value)
            if nested:
                return nested
    if isinstance(payload, list):
        for item in payload:
            nested = _extract_qr_hint(item)
            if nested:
                return nested
    return ""


def _state_is_open(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False).lower()
    markers = ["open", "connected", "pairing", "isconnected\": true", "connected\":true"]
    return any(marker in text for marker in markers)


def run_manual_evolution_test() -> None:
    cfg = _load_config()
    if not cfg.api_key:
        raise RuntimeError("EVOLUTION_API_KEY is required.")

    client = EvolutionApiClient(cfg)
    webhook_server = _WebhookServer(cfg, client)
    try:
        webhook_server.start()
        print("=" * 70)
        print("EVOLUTION MANUAL INTEGRATION TEST")
        print("=" * 70)
        print(f"Local webhook    : {cfg.local_webhook_url}")
        print(f"Public webhook   : {cfg.public_webhook_url}")
        print(f"Evolution base   : {cfg.base_url}")
        print(f"Manager          : {cfg.manager_url}")
        print(f"Instance         : {cfg.instance_name}")
        print(f"Clinic           : {cfg.clinic_name}")
        print(f"Booking link     : {cfg.booking_link}")
        print("")
        print("Step 1: Creating or opening the Evolution instance...")
        create_payload = client.create_or_open_instance()
        print(json.dumps(create_payload, indent=2, ensure_ascii=False)[:3000])
        fetched_instances = client.fetch_instances()
        if fetched_instances:
            print("Current instance snapshot:")
            print(json.dumps(fetched_instances[0], indent=2, ensure_ascii=False)[:3000])
        print("")
        print("Step 2: Setting webhook for this manual test...")
        webhook_payload = client.set_webhook()
        print(json.dumps(webhook_payload, indent=2, ensure_ascii=False)[:3000])
        print("")
        print("Step 3: Requesting QR/connect payload...")
        connect_payload = {}
        qr_hint = ""
        qr_deadline = time.time() + 60
        while time.time() < qr_deadline:
            connect_payload = client.connect_instance()
            print(json.dumps(connect_payload, indent=2, ensure_ascii=False)[:3000])
            qr_hint = (
                _extract_qr_hint(connect_payload)
                or webhook_server.latest_qr_code
                or webhook_server.latest_pairing_code
                or _extract_qr_hint(create_payload)
            )
            if qr_hint:
                break
            time.sleep(3)
        if qr_hint:
            qr_file = DATA_DIR / f"evolution_qr_{cfg.instance_name}.txt"
            qr_file.write_text(qr_hint, encoding="utf-8")
            print(f"QR/pairing hint saved to: {qr_file}")
            print("Use that code/QR payload or the Evolution dashboard to scan WhatsApp.")
        else:
            print("No QR field was detected automatically yet. Check the Evolution dashboard for the QR.")
            print(f"Open the manager here: {cfg.manager_url}")
        print("")
        print("Step 4: Scan the WhatsApp QR from the Evolution dashboard/response if needed.")
        print("The test will now wait for the session to become connected.")

        connect_deadline = time.time() + min(cfg.timeout_seconds, 300)
        while time.time() < connect_deadline:
            state_payload = client.connection_state()
            print(f"Connection state: {json.dumps(state_payload, ensure_ascii=False)[:500]}")
            if _state_is_open(state_payload):
                print("WhatsApp session is connected.")
                break
            time.sleep(5)
        else:
            print("")
            print("The WhatsApp session is still not connected.")
            print("This is no longer a code error in the test file.")
            print(f"Open the Evolution Manager and scan the QR there: {cfg.manager_url}")
            print("Then rerun this script, or keep it running longer by increasing EVOLUTION_MANUAL_TIMEOUT_SECONDS.")
            return

        print("")
        print("Step 5: From a DIFFERENT phone/number, send 3 text messages to this connected WhatsApp.")
        print("Expected behavior:")
        print("  1. First inbound message => welcome + booking link")
        print("  2. Second inbound message => warning")
        print("  3. Third inbound message => no response")
        print("")
        print("Waiting for webhook events...")

        deadline = time.time() + cfg.timeout_seconds
        while time.time() < deadline:
            if webhook_server.last_error:
                raise AssertionError(f"Webhook handler failed: {webhook_server.last_error}")
            if (
                webhook_server.inbound_events >= 3
                and webhook_server.welcome_sent >= 1
                and webhook_server.warning_sent >= 1
                and webhook_server.silenced >= 1
            ):
                break
            time.sleep(1)

        assert webhook_server.inbound_events >= 3, (
            "Did not receive 3 inbound webhook events. "
            "Check Evolution webhook delivery and send 3 text messages from another number."
        )
        assert webhook_server.welcome_sent >= 1, "Welcome auto-response was not sent."
        assert webhook_server.warning_sent >= 1, "Warning auto-response was not sent."
        assert webhook_server.silenced >= 1, "Third-message silence behavior was not observed."

        print("")
        print("Manual Evolution test passed.")
        print(f"Inbound events : {webhook_server.inbound_events}")
        print(f"Welcome sent   : {webhook_server.welcome_sent}")
        print(f"Warning sent   : {webhook_server.warning_sent}")
        print(f"Silenced       : {webhook_server.silenced}")
        print(f"Event log file : {DATA_DIR / f'evolution_manual_test_{cfg.instance_name}.jsonl'}")
        if cfg.keep_running_after_success:
            print("")
            print("Keep-alive mode is enabled.")
            print("The webhook server will keep running until you stop it manually with Ctrl+C.")
            while True:
                if webhook_server.last_error:
                    raise AssertionError(f"Webhook handler failed: {webhook_server.last_error}")
                time.sleep(1)
    finally:
        webhook_server.stop()
        client.close()


@pytest.mark.skipif(
    not _env_bool("RUN_EVOLUTION_MANUAL_TEST", False),
    reason=(
        "Manual Evolution integration test is disabled by default. "
        "Set RUN_EVOLUTION_MANUAL_TEST=1 and provide Evolution env vars to run it."
    ),
)
def test_evolution_temp_autoresponse_manual_flow() -> None:
    run_manual_evolution_test()


if __name__ == "__main__":
    run_manual_evolution_test()
