from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Optional


LOGGER = logging.getLogger(__name__)


class KafkaNotificationBridge:
    def __init__(
        self,
        *,
        settings: Any,
        logger: Any,
        process_event_fn: Callable[[Any], bool],
        producer_cls: Any = None,
        consumer_cls: Any = None,
        event_cls: Any = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or LOGGER
        self._process_event_fn = process_event_fn
        self._producer_cls = producer_cls
        self._consumer_cls = consumer_cls
        self._event_cls = event_cls
        self._producer: Optional[Any] = None
        self._consumer: Optional[Any] = None
        self._stop = threading.Event()
        self._consumer_thread: Optional[threading.Thread] = None
        self._published = 0
        self._publish_failed = 0
        self._consumed = 0
        self._enabled = bool(
            getattr(settings, "kafka_enabled", False)
            and (getattr(settings, "kafka_bootstrap_servers", "") or "").strip()
        )
        self._bootstrap_servers = (getattr(settings, "kafka_bootstrap_servers", "") or "").strip()
        self._topic = (getattr(settings, "kafka_notification_topic", "msgbot.notifications") or "msgbot.notifications").strip()
        self._group = (getattr(settings, "kafka_notification_consumer_group", "msgbot-notification-workers") or "msgbot-notification-workers").strip()
        self._poll_timeout_ms = max(100, int(getattr(settings, "kafka_poll_timeout_ms", 1000)))

    def _load_kafka_classes(self) -> tuple[Any, Any]:
        if self._producer_cls and self._consumer_cls:
            return self._producer_cls, self._consumer_cls
        try:
            from kafka import KafkaConsumer, KafkaProducer

            return KafkaProducer, KafkaConsumer
        except Exception as exc:
            self._logger.warning("Kafka notification bridge disabled; client library unavailable error=%s", exc)
            self._enabled = False
            return None, None

    def start(self) -> None:
        if not self._enabled or self._consumer_thread:
            return
        producer_cls, consumer_cls = self._load_kafka_classes()
        if not producer_cls or not consumer_cls:
            return
        try:
            self._producer = producer_cls(
                bootstrap_servers=self._bootstrap_servers.split(","),
                acks="all",
                value_serializer=lambda value: value,
                key_serializer=lambda value: value.encode("utf-8") if isinstance(value, str) else value,
            )
            self._consumer = consumer_cls(
                self._topic,
                bootstrap_servers=self._bootstrap_servers.split(","),
                group_id=self._group,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                value_deserializer=lambda value: value,
                consumer_timeout_ms=1000,
            )
        except Exception as exc:
            self._logger.warning("Kafka notification bridge startup failed; using scheduler direct path error=%s", exc)
            self._enabled = False
            self._producer = None
            self._consumer = None
            return
        self._stop.clear()
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            name="kafka-notification-consumer",
            daemon=True,
        )
        self._consumer_thread.start()
        self._logger.info(
            "Kafka notification bridge enabled bootstrap=%s topic=%s group=%s",
            self._bootstrap_servers,
            self._topic,
            self._group,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=2.0)
        self._consumer_thread = None
        try:
            if self._consumer is not None:
                self._consumer.close()
        except Exception:
            pass
        try:
            if self._producer is not None:
                self._producer.flush(timeout=5)
                self._producer.close()
        except Exception:
            pass
        self._consumer = None
        self._producer = None

    def process_pending_events(self, events: list[Any]) -> tuple[int, int, int]:
        queued = 0
        sent = 0
        failed = 0
        for event in events:
            if not self._enabled or self._producer is None:
                if self._process_event_fn(event):
                    sent += 1
                else:
                    failed += 1
                continue
            try:
                future = self._producer.send(
                    self._topic,
                    key=str(getattr(event, "notification_id", "") or "notification"),
                    value=self._serialize_event(event),
                )
                future.get(timeout=5)
                self._published += 1
                queued += 1
            except Exception as exc:
                self._publish_failed += 1
                self._logger.warning(
                    "Kafka notification publish failed; using direct path notification_id=%s error=%s",
                    getattr(event, "notification_id", 0),
                    exc,
                )
                if self._process_event_fn(event):
                    sent += 1
                else:
                    failed += 1
        return queued, sent, failed

    def snapshot(self) -> dict:
        return {
            "kafka_enabled": self._enabled,
            "kafka_topic": self._topic if self._enabled else "",
            "kafka_bootstrap_servers": self._bootstrap_servers if self._enabled else "",
            "kafka_published": self._published,
            "kafka_publish_failed": self._publish_failed,
            "kafka_consumed": self._consumed,
        }

    def _serialize_event(self, event: Any) -> bytes:
        if hasattr(event, "__dataclass_fields__"):
            payload = asdict(event)
        else:
            payload = dict(getattr(event, "__dict__", {}) or {})
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _deserialize_event(self, raw: bytes) -> Any:
        payload = json.loads(raw.decode("utf-8"))
        if self._event_cls is not None:
            return self._event_cls(**payload)
        return payload

    def _consumer_loop(self) -> None:
        consumer = self._consumer
        if consumer is None:
            return
        while not self._stop.is_set():
            try:
                records = consumer.poll(timeout_ms=self._poll_timeout_ms, max_records=20)
            except Exception as exc:
                self._logger.warning("Kafka notification poll failed error=%s", exc)
                time.sleep(1.0)
                continue
            if not records:
                continue
            for _topic_partition, messages in records.items():
                for message in messages:
                    try:
                        event = self._deserialize_event(message.value)
                        self._process_event_fn(event)
                        self._consumed += 1
                        try:
                            consumer.commit()
                        except Exception:
                            pass
                    except Exception as exc:
                        self._logger.warning("Kafka notification processing failed error=%s", exc)
