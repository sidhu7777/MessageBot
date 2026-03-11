from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Optional

from src.runtime.turn_queue import TurnTask


LOGGER = logging.getLogger(__name__)


class KafkaTurnBridge:
    def __init__(
        self,
        *,
        settings: Any,
        logger: Any,
        turn_processor: Any,
        producer_cls: Any = None,
        consumer_cls: Any = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or LOGGER
        self._turn_processor = turn_processor
        self._producer_cls = producer_cls
        self._consumer_cls = consumer_cls
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
        self._topic = (getattr(settings, "kafka_turn_topic", "msgbot.turns") or "msgbot.turns").strip()
        self._group = (getattr(settings, "kafka_turn_consumer_group", "msgbot-turn-workers") or "msgbot-turn-workers").strip()
        self._poll_timeout_ms = max(100, int(getattr(settings, "kafka_poll_timeout_ms", 1000)))

    def _load_kafka_classes(self) -> tuple[Any, Any]:
        if self._producer_cls and self._consumer_cls:
            return self._producer_cls, self._consumer_cls
        try:
            from kafka import KafkaConsumer, KafkaProducer

            return KafkaProducer, KafkaConsumer
        except Exception as exc:
            self._logger.warning("Kafka disabled; client library unavailable error=%s", exc)
            self._enabled = False
            return None, None

    @staticmethod
    def _serialize_task(task: TurnTask) -> bytes:
        payload = {
            "from_number": task.from_number,
            "body": task.body,
            "inbound_sid": task.inbound_sid,
            "pre_state": task.pre_state,
            "attempt": int(task.attempt),
            "enqueue_ts": float(task.enqueue_ts),
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _deserialize_task(raw: bytes) -> TurnTask:
        payload = json.loads(raw.decode("utf-8"))
        return TurnTask(
            from_number=str(payload.get("from_number") or ""),
            body=str(payload.get("body") or ""),
            inbound_sid=str(payload.get("inbound_sid") or ""),
            pre_state=str(payload.get("pre_state") or ""),
            attempt=max(0, int(payload.get("attempt") or 0)),
            enqueue_ts=float(payload.get("enqueue_ts") or time.time()),
        )

    def start(self) -> None:
        self._turn_processor.start()
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
            self._logger.warning("Kafka bridge startup failed; using in-process queue error=%s", exc)
            self._enabled = False
            self._producer = None
            self._consumer = None
            return
        self._stop.clear()
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            name="kafka-turn-consumer",
            daemon=True,
        )
        self._consumer_thread.start()
        self._logger.info(
            "Kafka turn bridge enabled bootstrap=%s topic=%s group=%s",
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
        self._turn_processor.stop()

    def submit(self, task: TurnTask) -> bool:
        return self._submit_task(task)

    def submit_overflow(self, task: TurnTask) -> bool:
        return self._submit_task(task)

    def _submit_task(self, task: TurnTask) -> bool:
        if not self._enabled or self._producer is None:
            return self._turn_processor.submit(task)
        try:
            future = self._producer.send(
                self._topic,
                key=(task.inbound_sid or task.from_number or "turn"),
                value=self._serialize_task(task),
            )
            future.get(timeout=5)
            self._published += 1
            return True
        except Exception as exc:
            self._publish_failed += 1
            self._logger.warning(
                "Kafka publish failed; falling back to in-process queue sid=%s from=%s error=%s",
                task.inbound_sid or "-",
                task.from_number,
                exc,
            )
            return self._turn_processor.submit(task)

    def backlog_size(self) -> int:
        return self._turn_processor.backlog_size()

    def snapshot(self) -> dict:
        payload = dict(self._turn_processor.snapshot())
        payload.update(
            {
                "kafka_enabled": self._enabled,
                "kafka_topic": self._topic if self._enabled else "",
                "kafka_bootstrap_servers": self._bootstrap_servers if self._enabled else "",
                "kafka_published": self._published,
                "kafka_publish_failed": self._publish_failed,
                "kafka_consumed": self._consumed,
            }
        )
        return payload

    def _consumer_loop(self) -> None:
        consumer = self._consumer
        if consumer is None:
            return
        while not self._stop.is_set():
            try:
                records = consumer.poll(timeout_ms=self._poll_timeout_ms, max_records=10)
            except Exception as exc:
                self._logger.warning("Kafka poll failed error=%s", exc)
                time.sleep(1.0)
                continue
            if not records:
                continue
            for _topic_partition, messages in records.items():
                for message in messages:
                    try:
                        task = self._deserialize_task(message.value)
                    except Exception as exc:
                        self._logger.warning("Kafka task decode failed error=%s", exc)
                        try:
                            consumer.commit()
                        except Exception:
                            pass
                        continue
                    while not self._stop.is_set():
                        if self._turn_processor.submit(task):
                            self._consumed += 1
                            try:
                                consumer.commit()
                            except Exception:
                                pass
                            break
                        time.sleep(0.2)
