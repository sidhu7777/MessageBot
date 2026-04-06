import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.repositories.notification_repository import NotificationEvent
from src.runtime.kafka_notification_bridge import KafkaNotificationBridge


class _FakeFuture:
    def get(self, timeout=None):
        return None


class _FakeProducer:
    def __init__(self, *args, **kwargs) -> None:
        self.sent = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
        return _FakeFuture()

    def flush(self, timeout=None):
        return None

    def close(self):
        return None


class _FakeMessage:
    def __init__(self, value: bytes) -> None:
        self.value = value


class _FakeConsumer:
    def __init__(self, *topics, **kwargs) -> None:
        self.records = []
        self.commits = 0

    def poll(self, timeout_ms=0, max_records=None):
        if self.records:
            batch = self.records
            self.records = []
            return {"tp": batch}
        time.sleep(0.05)
        return {}

    def commit(self):
        self.commits += 1

    def close(self):
        return None


def _event(notification_id: int = 1) -> NotificationEvent:
    return NotificationEvent(
        notification_id=notification_id,
        appointment_id=99,
        event_type="CANCELLED",
        channel="telegram",
        destination="telegram:123",
        status="PENDING",
        patient_name="Vineeth",
        clinic_name="Aditya",
        slot_date="2026-03-10",
        slot_time="10:00",
        patient_phone="9876543210",
        patient_telegram_chat_id="123",
        meta_json="",
        admin_id=1,
        attempt_count=0,
    )


def test_kafka_notification_bridge_falls_back_when_disabled() -> None:
    processed = []
    bridge = KafkaNotificationBridge(
        settings=SimpleNamespace(
            kafka_enabled=False,
            kafka_bootstrap_servers="localhost:9094",
            kafka_notification_topic="msgbot.notifications",
            kafka_notification_consumer_group="msgbot-notification-workers",
            kafka_poll_timeout_ms=50,
        ),
        logger=None,
        process_event_fn=lambda event: processed.append(event.notification_id) or True,
        producer_cls=_FakeProducer,
        consumer_cls=_FakeConsumer,
        event_cls=NotificationEvent,
    )
    bridge.start()
    queued, sent, failed = bridge.process_pending_events([_event(7)])
    bridge.stop()

    assert queued == 0
    assert sent == 1
    assert failed == 0
    assert processed == [7]


def test_kafka_notification_bridge_publishes_and_consumes() -> None:
    processed = []
    bridge = KafkaNotificationBridge(
        settings=SimpleNamespace(
            kafka_enabled=True,
            kafka_bootstrap_servers="localhost:9094",
            kafka_notification_topic="msgbot.notifications",
            kafka_notification_consumer_group="msgbot-notification-workers",
            kafka_poll_timeout_ms=50,
        ),
        logger=None,
        process_event_fn=lambda event: processed.append(event.notification_id) or True,
        producer_cls=_FakeProducer,
        consumer_cls=_FakeConsumer,
        event_cls=NotificationEvent,
    )
    bridge.start()
    event = _event(8)
    queued, sent, failed = bridge.process_pending_events([event])
    assert queued == 1
    assert sent == 0
    assert failed == 0
    assert bridge._producer is not None
    _topic, _key, raw = bridge._producer.sent[0]
    assert bridge._consumer is not None
    bridge._consumer.records.append(_FakeMessage(raw))

    deadline = time.time() + 2.0
    while time.time() < deadline and not processed:
        time.sleep(0.05)

    bridge.stop()
    assert processed == [8]
