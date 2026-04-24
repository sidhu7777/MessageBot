import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.automation.scheduler import AutomationScheduler


@dataclass
class _Event:
    notification_id: int
    appointment_id: int
    event_type: str
    channel: str
    destination: str
    status: str
    patient_name: str
    clinic_name: str
    slot_date: str
    slot_time: str
    patient_phone: str
    patient_telegram_chat_id: str
    meta_json: str
    admin_id: int | None
    doctor_id: int | None = None
    channel_account_id: int | None = None
    attempt_count: int = 0
    source_channel: str = ""
    doctor_name: str = ""
    doctor_slug: str = ""


class _FakeBookingRepo:
    def __init__(self) -> None:
        self.statuses = []
        self.retries = []

    def mark_notification_event_status(
        self,
        *,
        notification_id: int,
        status: str,
        error_text: str = "",
        provider_message_sid: str = "",
        channel_account_id=None,
        doctor_id=None,
        admin_id=None,
    ) -> None:
        self.statuses.append((notification_id, status, error_text, provider_message_sid))

    def mark_notification_event_retry(
        self,
        *,
        notification_id: int,
        error_text: str,
        backoff_seconds: int,
        max_attempts: int,
    ) -> None:
        self.retries.append((notification_id, error_text, backoff_seconds, max_attempts))


def test_sms_notification_skips_when_source_channel_not_enabled() -> None:
    repo = _FakeBookingRepo()
    sent_payloads = []

    scheduler = AutomationScheduler(
        settings=SimpleNamespace(
            sms_enabled=True,
            sms_api_url="http://sms.example/send",
            sms_api_key="key",
            sms_sender="Dappto",
            sms_message_type="TXT",
            sms_response="Y",
            sms_enabled_channels="qr_scan",
            frontend_base_url="https://example.test/book",
        ),
        booking_repository=repo,  # type: ignore[arg-type]
        send_message_fn=lambda to_number, body: sent_payloads.append((to_number, body)),
        enabled=False,
        doctor_reminder_enabled=False,
    )

    event = _Event(
        notification_id=1,
        appointment_id=100,
        event_type="CONFIRMATION",
        channel="sms",
        destination="919999999999",
        status="PENDING",
        patient_name="Aman",
        clinic_name="City Clinic",
        slot_date="2026-04-21",
        slot_time="10:30",
        patient_phone="919999999999",
        patient_telegram_chat_id="",
        meta_json="",
        admin_id=1,
        doctor_id=7,
        source_channel="telegram",
        doctor_name="Sharma",
    )

    processed = scheduler._process_notification_event(event)  # type: ignore[arg-type]

    assert processed is True
    assert sent_payloads == []
    assert repo.retries == []
    assert repo.statuses == [
        (1, "SKIPPED", "SMS disabled for source channel 'telegram'.", "")
    ]


def test_sms_notification_uses_doctor_name_and_enabled_source_channel(monkeypatch) -> None:
    repo = _FakeBookingRepo()
    sms_calls = []

    scheduler = AutomationScheduler(
        settings=SimpleNamespace(
            sms_enabled=True,
            sms_api_url="http://sms.example/send",
            sms_api_key="key",
            sms_sender="Dappto",
            sms_message_type="TXT",
            sms_response="Y",
            sms_enabled_channels="qr_scan",
            frontend_base_url="https://example.test/book",
        ),
        booking_repository=repo,  # type: ignore[arg-type]
        send_message_fn=lambda to_number, body: None,
        enabled=False,
        doctor_reminder_enabled=False,
    )

    from src.runtime.sms_notification_service import SMSNotificationService

    def _fake_send_sms(self, phone_number: str, message: str, meta_json: str = ""):
        sms_calls.append((phone_number, message))
        return True, "SMS123"

    monkeypatch.setattr(SMSNotificationService, "send_sms", _fake_send_sms)

    event = _Event(
        notification_id=2,
        appointment_id=101,
        event_type="CONFIRMATION",
        channel="sms",
        destination="919888888888",
        status="PENDING",
        patient_name="Aman",
        clinic_name="City Clinic",
        slot_date="2026-04-21",
        slot_time="10:30",
        patient_phone="919888888888",
        patient_telegram_chat_id="",
        meta_json="",
        admin_id=1,
        doctor_id=7,
        source_channel="qr_scan",
        doctor_name="Sharma",
        doctor_slug="Dr.Sharma",
    )

    processed = scheduler._process_notification_event(event)  # type: ignore[arg-type]

    assert processed is True
    assert repo.retries == []
    assert repo.statuses == [(2, "SENT", "", "SMS123")]
    assert len(sms_calls) == 1
    assert sms_calls[0][0] == "919888888888"
    assert "Dr. Sharma" in sms_calls[0][1]
    assert "https://example.test/book" in sms_calls[0][1]


def test_qr_sms_confirmation_never_uses_channel_sender(monkeypatch) -> None:
    repo = _FakeBookingRepo()
    channel_sender_calls = []
    sms_credit_calls = []

    scheduler = AutomationScheduler(
        settings=SimpleNamespace(
            sms_enabled=True,
            sms_api_url="http://sms.example/send",
            sms_api_key="key",
            sms_sender="Dappto",
            sms_message_type="TXT",
            sms_response="Y",
            sms_enabled_channels="qr_scan",
            frontend_base_url="https://example.test/book",
        ),
        booking_repository=repo,  # type: ignore[arg-type]
        send_message_fn=lambda to_number, body: channel_sender_calls.append((to_number, body)),
        enabled=False,
        doctor_reminder_enabled=False,
    )

    from src.runtime.sms_notification_service import SMSNotificationService

    def _fake_send_sms_with_credit_check(self, *, doctor_id, appointment_id, phone_number, message, meta_json=""):
        sms_credit_calls.append((doctor_id, appointment_id, phone_number, message))
        return True, "SMS266", ""

    monkeypatch.setattr(SMSNotificationService, "send_sms_with_credit_check", _fake_send_sms_with_credit_check)

    event = _Event(
        notification_id=103,
        appointment_id=266,
        event_type="CONFIRMATION",
        channel="'sms'",
        destination="9392569600",
        status="PENDING",
        patient_name="Vineeth",
        clinic_name="Clinicone",
        slot_date="2026-04-23",
        slot_time="11:45",
        patient_phone="9392569600",
        patient_telegram_chat_id="",
        meta_json='{"source_channel": "qr_scan"}',
        admin_id=1,
        doctor_id=4,
        attempt_count=1,
        source_channel="qr_scan",
        doctor_name="Aman",
        doctor_slug="Dr.Aman",
    )

    processed = scheduler._process_notification_event(event)  # type: ignore[arg-type]

    assert processed is True
    assert channel_sender_calls == []
    assert len(sms_credit_calls) == 1
    assert sms_credit_calls[0][2] == "9392569600"
    assert "https://example.test/book" in sms_credit_calls[0][3]
    assert repo.statuses == [(103, "SENT", "", "SMS266")]
