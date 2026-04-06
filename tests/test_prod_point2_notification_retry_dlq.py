import sys
from dataclasses import dataclass
from pathlib import Path

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
    attempt_count: int = 0


class _FakeBookingRepo:
    def __init__(self):
        self.retried = []
        self.sent = []
        self._events = [
            _Event(1, 101, "CANCELLED", "auto", "", "PENDING", "p1", "c1", "2026-02-24", "10:00", "", "", "", 1, 0),
            _Event(2, 102, "RESCHEDULED", "auto", "", "PENDING", "p2", "c2", "2026-02-24", "11:00", "9392929292", "", "", 1, 0),
        ]

    def claim_pending_notification_events(self, *, limit: int, worker_id: str, admin_id=None):
        return self._events[:limit]

    def mark_notification_event_retry(self, *, notification_id: int, error_text: str, backoff_seconds: int, max_attempts: int):
        self.retried.append((notification_id, backoff_seconds, max_attempts, error_text))

    def mark_notification_event_status(self, *, notification_id: int, status: str, error_text: str = "", provider_message_sid: str = ""):
        self.sent.append((notification_id, status, provider_message_sid))


def test_notification_retry_and_sent_paths() -> None:
    repo = _FakeBookingRepo()

    sent_payloads = []

    def _send(to_number: str, body: str):
        sent_payloads.append((to_number, body))
        if "whatsapp:" in to_number:
            return "SM_TEST"
        raise RuntimeError("should not happen")

    scheduler = AutomationScheduler(
        booking_repository=repo,  # type: ignore[arg-type]
        send_message_fn=_send,
        send_document_fn=None,
        source_whatsapp_number="",
        enabled=False,
        doctor_reminder_enabled=False,
    )

    scheduler._run_event_notifications_once()

    # First event has no destination -> retry with fixed backoff
    assert any(row[0] == 1 and row[1] == 120 for row in repo.retried)
    # Second event is sent successfully
    assert any(row[0] == 2 and row[1] == "SENT" and row[2] == "SM_TEST" for row in repo.sent)
    assert len(sent_payloads) == 1

