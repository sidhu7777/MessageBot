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
    dead: bool = False


class _InMemoryRepo:
    def __init__(self):
        self.events = {
            # No destination -> should retry then DEAD
            1: _Event(1, 101, "CANCELLED", "auto", "", "PENDING", "A", "Clinic", "2026-02-24", "10:00", "", "", "", 1),
            # Destination exists but sender fails -> retry then DEAD
            2: _Event(2, 102, "RESCHEDULED", "auto", "", "PENDING", "B", "Clinic", "2026-02-24", "10:30", "9392929292", "", "", 1),
            # Destination exists and send succeeds -> SENT
            3: _Event(3, 103, "DOCTOR_DELAYED", "auto", "", "PENDING", "C", "Clinic", "2026-02-24", "11:00", "9292929282", "", '{"delay_minutes":10}', 1),
        }
        self.retry_calls = []
        self.sent_calls = []

    def claim_pending_notification_events(self, *, limit: int, worker_id: str, admin_id=None):
        out = []
        for ev in self.events.values():
            if ev.dead:
                continue
            if ev.status in {"PENDING", "FAILED"}:
                ev.status = "PROCESSING"
                out.append(ev)
            if len(out) >= limit:
                break
        return out

    def mark_notification_event_retry(self, *, notification_id: int, error_text: str, backoff_seconds: int, max_attempts: int):
        ev = self.events[notification_id]
        ev.attempt_count += 1
        self.retry_calls.append((notification_id, ev.attempt_count, backoff_seconds, max_attempts, error_text))
        if ev.attempt_count >= max_attempts:
            ev.status = "DEAD"
            ev.dead = True
        else:
            ev.status = "FAILED"

    def mark_notification_event_status(self, *, notification_id: int, status: str, error_text: str = "", provider_message_sid: str = ""):
        ev = self.events[notification_id]
        ev.status = status
        self.sent_calls.append((notification_id, status, provider_message_sid))


def test_hard_notification_retry_and_deadletter_lifecycle() -> None:
    repo = _InMemoryRepo()
    sent_payloads = []

    def _send_message(to_number: str, body: str):
        sent_payloads.append((to_number, body))
        if "9292929282" in to_number:
            return "SM_OK"
        raise RuntimeError("provider down")

    scheduler = AutomationScheduler(
        booking_repository=repo,  # type: ignore[arg-type]
        send_message_fn=_send_message,
        send_document_fn=None,
        enabled=False,
        doctor_reminder_enabled=False,
    )

    # Run multiple cycles to force retries into DEAD.
    for _ in range(7):
        scheduler._run_event_notifications_once()

    # Event 3 should be sent once.
    assert any(row[0] == 3 and row[1] == "SENT" and row[2] == "SM_OK" for row in repo.sent_calls)

    # Event 1 and 2 should hit DEAD after max attempts.
    assert repo.events[1].dead is True
    assert repo.events[2].dead is True

    # Backoff for event 2 should be exponential-like and non-decreasing.
    e2_backoffs = [row[2] for row in repo.retry_calls if row[0] == 2]
    assert len(e2_backoffs) >= 5
    assert all(e2_backoffs[i] <= e2_backoffs[i + 1] for i in range(len(e2_backoffs) - 1))
    assert e2_backoffs[0] == 60
    assert e2_backoffs[1] == 120

