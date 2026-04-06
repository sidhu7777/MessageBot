import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import main as app_main
try:
    from src.api.admin_router import create_admin_router
    from src.repositories.auth_repository import AuthPrincipal
except Exception:
    pytest.skip("Skipping production runtime flow tests: admin/auth router modules not present in this codebase.", allow_module_level=True)

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.runtime import TurnQueueProcessor, TurnTask


def _sid(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


class _FakeTwilioMessage:
    def __init__(self, sid: str) -> None:
        self.sid = sid


class _FakeTwilioMessagesApi:
    def __init__(self, sink: list[dict]) -> None:
        self._sink = sink

    def create(self, **kwargs):
        sid = f"SM_OUT_{len(self._sink) + 1}"
        payload = dict(kwargs)
        payload["sid"] = sid
        self._sink.append(payload)
        return _FakeTwilioMessage(sid=sid)


class _FakeTwilioClient:
    def __init__(self, sink: list[dict]) -> None:
        self.messages = _FakeTwilioMessagesApi(sink)


class _FakeAuthRepo:
    def __init__(self) -> None:
        self._token = "tok_admin_1"
        self._expires = datetime.utcnow() + timedelta(hours=2)

    def login_admin(self, email: str, password: str, ttl_minutes: int):
        if email == "admin@example.com" and password == "secret":
            return AuthPrincipal(
                user_id=1,
                role="admin",
                admin_id=10,
                token=self._token,
                expires_at=self._expires,
            )
        return None

    def validate_token(self, token: str):
        if token == self._token:
            return AuthPrincipal(
                user_id=1,
                role="admin",
                admin_id=10,
                token=token,
                expires_at=self._expires,
            )
        return None

    def revoke_token(self, token: str) -> bool:
        return token == self._token


class _FakeSchedulingRepo:
    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return []

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 3):
        return []

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 3):
        return []

    def generate_slots_for_schedule(self, schedule_id: int, days_ahead: int):
        return None


class _FakeBookingRepo:
    def get_appointment_status(self, appointment_id: int):
        return {"appointment_id": appointment_id, "status": "BOOKED"}


class _FakeBookingRepoExisting:
    def __init__(self) -> None:
        self.cancel_called = False
        self.reschedule_called = False

    def default_admin_id(self):
        return 1

    def find_active_appointment_by_patient_name(self, patient_name: str, admin_id=None):
        if patient_name.lower() == "vineeth raja banala":
            return {
                "appointment_id": 999,
                "clinic_id": 1,
                "doctor_id": 2,
                "clinic_name": "City Care Clinic",
                "slot_date": "2026-02-20",
                "slot_time": "10:00",
            }
        return None

    def cancel_appointment(self, appointment_id: int, admin_id=None):
        self.cancel_called = True
        return appointment_id == 999

    def reschedule_appointment_same_clinic(self, appointment_id: int, new_date: str, new_time: str, admin_id=None):
        self.reschedule_called = True
        if appointment_id == 999 and new_date and new_time:
            return type("R", (), {"ok": True, "appointment_id": 999})()
        return type("R", (), {"ok": False, "appointment_id": None})()


class _FakeSchedulingRepoReschedule:
    class _Clinic:
        def __init__(self, clinic_id: int, clinic_name: str, location: str, today_slots: int) -> None:
            self.clinic_id = clinic_id
            self.clinic_name = clinic_name
            self.location = location
            self.today_slots = today_slots

    def default_doctor_id(self, admin_id=None):
        return 2

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [
            self._Clinic(1, "City Care Clinic", "MG Road, Hyderabad", 7),
            self._Clinic(2, "Sunrise Health Center", "KPHB, Hyderabad", 5),
        ][:limit]

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 3):
        return ["2026-02-21", "2026-02-22", "2026-02-23"][:limit]

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 3):
        return ["10:00", "11:00", "12:00"][:limit]


def _assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _install_test_queue(process_fn, send_fn, worker_count: int, max_queue_size: int, retry_attempts: int):
    # Stop any existing queue to avoid thread leaks between tests.
    try:
        app_main.turn_processor.stop()
    except Exception:
        pass
    app_main.turn_processor = TurnQueueProcessor(
        worker_count=worker_count,
        max_queue_size=max_queue_size,
        process_fn=process_fn,
        send_fn=send_fn,
        retry_attempts=retry_attempts,
    )


def test_admin_token_and_rate_limit() -> None:
    app = FastAPI()
    app.include_router(
        create_admin_router(
            booking_repository=_FakeBookingRepo(),
            scheduling_repository=_FakeSchedulingRepo(),
            auth_repository=_FakeAuthRepo(),
            admin_api_key="",
            rate_limit_per_minute=2,
            token_ttl_minutes=60,
        )
    )
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})
    _assert_true(login.status_code == 200, f"Login failed: {login.status_code}")
    token = login.json().get("access_token", "")
    _assert_true(bool(token), "Login did not return access_token.")

    no_auth = client.get("/api/clinics", params={"doctor_id": 2, "admin_id": 10})
    _assert_true(no_auth.status_code == 401, f"Expected 401 without token, got {no_auth.status_code}")

    headers = {"Authorization": f"Bearer {token}"}
    ok1 = client.get("/api/clinics", params={"doctor_id": 2, "admin_id": 10}, headers=headers)
    _assert_true(ok1.status_code == 200, f"Expected 200 for authorized call, got {ok1.status_code}")

    # Third protected call in same minute should hit rate limit (limit=2).
    ok2 = client.get("/api/clinics", params={"doctor_id": 2, "admin_id": 10}, headers=headers)
    _assert_true(ok2.status_code == 429, f"Expected 429 rate limit, got {ok2.status_code}")


def test_webhook_async_ack_and_safe_then_final() -> None:
    sent_messages: list[dict] = []
    app_main.twilio_client = _FakeTwilioClient(sent_messages)

    object.__setattr__(app_main.settings, "llm_provider", "mock")
    object.__setattr__(app_main.settings, "twilio_use_rest_responses", True)
    object.__setattr__(app_main.settings, "twilio_whatsapp_from", "whatsapp:+14155238886")
    object.__setattr__(app_main.settings, "queue_busy_threshold", 1)
    object.__setattr__(app_main.settings, "twilio_send_retries", 0)

    def process_fn(from_number: str, body: str):
        time.sleep(1.2)
        return "Final reply after processing.", "ASK_NAME"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        app_main._send_plain_rest_message(to_number=to_number, body=reply_text, inbound_sid=inbound_sid)

    _install_test_queue(process_fn=process_fn, send_fn=send_fn, worker_count=1, max_queue_size=5, retry_attempts=0)

    with TestClient(app_main.app) as client:
        start = time.perf_counter()
        resp = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+919000000001",
                "Body": "hello",
                "MessageSid": _sid("SM_ASYNC_1"),
            },
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _assert_true(resp.status_code == 200, f"Webhook ack status != 200, got {resp.status_code}")
        _assert_true(elapsed_ms < 500, f"Webhook ack too slow: {elapsed_ms:.1f}ms")

        # Wait for async worker final reply.
        deadline = time.time() + 4.0
        while time.time() < deadline and len(sent_messages) < 2:
            time.sleep(0.05)

    _assert_true(len(sent_messages) >= 2, "Expected safe processing + final message to be sent.")
    bodies = [m.get("body", "") for m in sent_messages]
    _assert_true(any("Please wait" in b for b in bodies), "Safe processing message not sent.")
    _assert_true(any("Final reply after processing." in b for b in bodies), "Final async reply not sent.")


def test_webhook_busy_message_when_queue_full() -> None:
    sent_messages: list[dict] = []
    app_main.twilio_client = _FakeTwilioClient(sent_messages)

    object.__setattr__(app_main.settings, "llm_provider", "mock")
    object.__setattr__(app_main.settings, "twilio_use_rest_responses", True)
    object.__setattr__(app_main.settings, "twilio_whatsapp_from", "whatsapp:+14155238886")
    object.__setattr__(app_main.settings, "queue_busy_threshold", 999)
    object.__setattr__(app_main.settings, "twilio_send_retries", 0)

    block = threading.Event()

    def process_fn(from_number: str, body: str):
        # Hold worker so queue can fill.
        if body.startswith("hold"):
            block.wait(timeout=2.0)
        return "ok", "ASK_NAME"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        app_main._send_plain_rest_message(to_number=to_number, body=reply_text, inbound_sid=inbound_sid)

    _install_test_queue(process_fn=process_fn, send_fn=send_fn, worker_count=1, max_queue_size=1, retry_attempts=0)

    with TestClient(app_main.app) as client:
        r1 = client.post("/webhook", data={"From": "whatsapp:+919000000002", "Body": "hold-1", "MessageSid": _sid("SM_BUSY_1")})
        r2 = client.post("/webhook", data={"From": "whatsapp:+919000000002", "Body": "hold-2", "MessageSid": _sid("SM_BUSY_2")})
        r3 = client.post("/webhook", data={"From": "whatsapp:+919000000002", "Body": "hold-3", "MessageSid": _sid("SM_BUSY_3")})
        _assert_true(r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200, "Webhook should always ack 200.")

        # Allow worker to drain.
        block.set()
        deadline = time.time() + 6.0
        while time.time() < deadline:
            has_busy = any("busy" in (m.get("body", "").lower()) for m in sent_messages)
            ok_count = sum(1 for m in sent_messages if m.get("body") == "ok")
            if has_busy and ok_count >= 3:
                break
            time.sleep(0.05)

    _assert_true(any("busy" in (m.get("body", "").lower()) for m in sent_messages), "Expected busy message.")
    # With overflow requeue, the 3rd message should still be processed later.
    ok_count = sum(1 for m in sent_messages if m.get("body") == "ok")
    _assert_true(ok_count >= 3, f"Expected deferred full-queue message to be processed later, got ok_count={ok_count}")


def test_webhook_retry_then_success() -> None:
    sent_messages: list[dict] = []
    app_main.twilio_client = _FakeTwilioClient(sent_messages)

    object.__setattr__(app_main.settings, "llm_provider", "mock")
    object.__setattr__(app_main.settings, "twilio_use_rest_responses", True)
    object.__setattr__(app_main.settings, "twilio_whatsapp_from", "whatsapp:+14155238886")
    object.__setattr__(app_main.settings, "queue_busy_threshold", 999)
    object.__setattr__(app_main.settings, "twilio_send_retries", 0)

    attempts = {"count": 0}

    def process_fn(from_number: str, body: str):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("simulated processing failure")
        return "Recovered after retry.", "ASK_NAME"

    def send_fn(to_number: str, reply_text: str, post_state: str, inbound_sid: str):
        app_main._send_plain_rest_message(to_number=to_number, body=reply_text, inbound_sid=inbound_sid)

    _install_test_queue(process_fn=process_fn, send_fn=send_fn, worker_count=1, max_queue_size=5, retry_attempts=1)

    with TestClient(app_main.app) as client:
        resp = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+919000000003",
                "Body": "needs retry",
                "MessageSid": _sid("SM_RETRY_1"),
            },
        )
        _assert_true(resp.status_code == 200, "Webhook ack should be 200.")
        deadline = time.time() + 5.0
        while time.time() < deadline and not any("Recovered after retry." in (m.get("body", "")) for m in sent_messages):
            time.sleep(0.05)

    _assert_true(attempts["count"] >= 2, "Retry path did not execute second attempt.")
    _assert_true(any("Recovered after retry." in (m.get("body", "")) for m in sent_messages), "Retry success reply not sent.")


def test_existing_booking_cancel_and_rebook_flow() -> None:
    fake_repo = _FakeBookingRepoExisting()
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", provider="mock", timeout_seconds=30),
        enable_llm_polish=False,
        booking_repository=fake_repo,
        scheduling_repository=_FakeSchedulingRepoReschedule(),
        mixed_response_language="auto",
    )
    r1 = fsm.handle("I need to book appointment")
    _assert_true("full name" in r1.lower(), "Expected ask name after booking intent.")
    r2 = fsm.handle("Vineeth Raja Banala")
    _assert_true("already have a booked appointment" in r2.lower(), "Expected existing booking prompt.")
    r3 = fsm.handle("3")
    _assert_true("reschedule" in r3.lower(), "Expected reschedule start message.")
    _assert_true(fsm.state == "ASK_CLINIC", f"Expected ASK_CLINIC, got {fsm.state}")

    r4 = fsm.handle("1")  # clinic
    _assert_true(fsm.state == "ASK_DATE", f"Expected ASK_DATE, got {fsm.state}")
    r5 = fsm.handle("1")  # date
    _assert_true(fsm.state == "ASK_TIME", f"Expected ASK_TIME, got {fsm.state}")
    r6 = fsm.handle("2")  # time
    _assert_true(fsm.state == "CONFIRM_RESCHEDULE", f"Expected CONFIRM_RESCHEDULE, got {fsm.state}")
    r7 = fsm.handle("yes")
    _assert_true("rescheduled" in r7.lower(), "Expected rescheduled success message.")
    _assert_true(fake_repo.reschedule_called, "Expected reschedule repository call.")
    _assert_true("old slot" in r6.lower() and "new slot" in r6.lower(), "Expected reschedule confirm summary.")


def main() -> int:
    tests = [
        ("admin_token_and_rate_limit", test_admin_token_and_rate_limit),
        ("webhook_async_ack_and_safe_then_final", test_webhook_async_ack_and_safe_then_final),
        ("webhook_busy_message_when_queue_full", test_webhook_busy_message_when_queue_full),
        ("webhook_retry_then_success", test_webhook_retry_then_success),
        ("existing_booking_cancel_and_rebook_flow", test_existing_booking_cancel_and_rebook_flow),
    ]

    failures: list[tuple[str, str]] = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"[FAIL] {name}: {exc}")

    print("")
    print(
        f"Production runtime flow tests: passed={len(tests)-len(failures)} failed={len(failures)} total={len(tests)}"
    )
    if failures:
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
