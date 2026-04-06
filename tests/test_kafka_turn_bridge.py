import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.kafka_turn_bridge import KafkaTurnBridge
from src.runtime.turn_queue import TurnTask


class _FakeTurnProcessor:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.submitted = []

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def submit(self, task: TurnTask) -> bool:
        self.submitted.append(task)
        return True

    def backlog_size(self) -> int:
        return len(self.submitted)

    def snapshot(self) -> dict:
        return {"processed": len(self.submitted)}


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
        self.closed = 0

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
        self.closed += 1


def test_kafka_turn_bridge_falls_back_when_disabled() -> None:
    settings = SimpleNamespace(
        kafka_enabled=False,
        kafka_bootstrap_servers="localhost:9094",
        kafka_turn_topic="msgbot.turns",
        kafka_turn_consumer_group="test-group",
        kafka_poll_timeout_ms=100,
    )
    processor = _FakeTurnProcessor()
    bridge = KafkaTurnBridge(
        settings=settings,
        logger=None,
        turn_processor=processor,
        producer_cls=_FakeProducer,
        consumer_cls=_FakeConsumer,
    )

    bridge.start()
    task = TurnTask(from_number="telegram:1", body="hello", inbound_sid="sid1", pre_state="INIT")
    assert bridge.submit(task) is True
    bridge.stop()

    assert processor.started == 1
    assert len(processor.submitted) == 1
    assert processor.submitted[0].inbound_sid == "sid1"


def test_kafka_turn_bridge_publishes_and_consumes() -> None:
    settings = SimpleNamespace(
        kafka_enabled=True,
        kafka_bootstrap_servers="localhost:9094",
        kafka_turn_topic="msgbot.turns",
        kafka_turn_consumer_group="test-group",
        kafka_poll_timeout_ms=50,
    )
    processor = _FakeTurnProcessor()
    bridge = KafkaTurnBridge(
        settings=settings,
        logger=None,
        turn_processor=processor,
        producer_cls=_FakeProducer,
        consumer_cls=_FakeConsumer,
    )

    bridge.start()
    task = TurnTask(from_number="telegram:2", body="book", inbound_sid="sid2", pre_state="INIT")
    assert bridge.submit(task) is True
    assert bridge._producer is not None
    assert len(bridge._producer.sent) == 1

    sent_topic, _sent_key, sent_value = bridge._producer.sent[0]
    assert sent_topic == "msgbot.turns"
    assert bridge._consumer is not None
    bridge._consumer.records.append(_FakeMessage(sent_value))

    deadline = time.time() + 2.0
    while time.time() < deadline and not processor.submitted:
        time.sleep(0.05)

    bridge.stop()

    assert len(processor.submitted) == 1
    assert processor.submitted[0].from_number == "telegram:2"
    assert processor.submitted[0].body == "book"
