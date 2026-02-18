import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository


LOGGER = logging.getLogger(__name__)


class _PersistentKeyStore:
    def __init__(self, path: str, max_entries: int = 100000) -> None:
        self._path = path
        self._max_entries = max(1000, max_entries)
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._ordered: list[str] = []
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    key = str(row.get("key", "")).strip()
                except Exception:
                    key = line
                if key and key not in self._seen:
                    self._seen.add(key)
                    self._ordered.append(key)
        self._trim_and_rewrite()

    def has(self, key: str) -> bool:
        normalized = (key or "").strip()
        if not normalized:
            return False
        with self._lock:
            return normalized in self._seen

    def add(self, key: str) -> None:
        normalized = (key or "").strip()
        if not normalized:
            return
        with self._lock:
            if normalized in self._seen:
                return
            self._seen.add(normalized)
            self._ordered.append(normalized)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"key": normalized, "ts": int(time.time())}) + "\n")
            if len(self._ordered) > self._max_entries:
                self._trim_and_rewrite()

    def _trim_and_rewrite(self) -> None:
        if len(self._ordered) > self._max_entries:
            self._ordered = self._ordered[-self._max_entries :]
            self._seen = set(self._ordered)
        with open(self._path, "w", encoding="utf-8") as handle:
            for key in self._ordered:
                handle.write(json.dumps({"key": key, "ts": int(time.time())}) + "\n")


class AutomationScheduler:
    def __init__(
        self,
        *,
        booking_repository: Optional[BookingRepository],
        scheduling_repository: Optional[SchedulingRepository],
        send_message_fn: Callable[[str, str], None],
        source_whatsapp_number: str = "",
        enabled: bool = True,
        slot_automation_enabled: bool = True,
        slot_generation_interval_seconds: int = 300,
        slot_generation_days_ahead: int = 30,
        doctor_reminder_enabled: bool = True,
        doctor_reminder_interval_seconds: int = 60,
        doctor_reminder_lead_minutes: int = 10,
        doctor_reminder_window_seconds: int = 30,
    ) -> None:
        self._booking_repository = booking_repository
        self._scheduling_repository = scheduling_repository
        self._send_message_fn = send_message_fn
        self._source_whatsapp_number = self._normalize_whatsapp_number(source_whatsapp_number)
        self._enabled = enabled
        self._slot_automation_enabled = slot_automation_enabled
        self._slot_generation_interval_seconds = max(30, int(slot_generation_interval_seconds))
        self._slot_generation_days_ahead = max(1, int(slot_generation_days_ahead))
        self._doctor_reminder_enabled = doctor_reminder_enabled
        self._doctor_reminder_interval_seconds = max(30, int(doctor_reminder_interval_seconds))
        self._doctor_reminder_lead_minutes = max(1, int(doctor_reminder_lead_minutes))
        self._doctor_reminder_window_seconds = max(5, int(doctor_reminder_window_seconds))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._metrics_lock = threading.Lock()
        self._metrics = {
            "slot_runs": 0,
            "slot_errors": 0,
            "slot_generated_for_schedules": 0,
            "slot_rebuild_runs": 0,
            "slot_rebuild_processed": 0,
            "slot_rebuild_deleted": 0,
            "reminder_runs": 0,
            "reminder_errors": 0,
            "reminder_sent": 0,
            "reminder_skipped": 0,
        }
        self._reminder_keys = _PersistentKeyStore(
            path=os.path.join("data", "doctor_reminder_keys.jsonl"),
            max_entries=200000,
        )

    def start(self) -> None:
        if not self._enabled or self._threads:
            return
        if self._slot_automation_enabled and self._scheduling_repository:
            thread = threading.Thread(target=self._slot_loop, name="slot-automation", daemon=True)
            thread.start()
            self._threads.append(thread)
        if self._doctor_reminder_enabled and self._booking_repository:
            thread = threading.Thread(target=self._reminder_loop, name="doctor-reminder", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()

    def snapshot(self) -> dict:
        with self._metrics_lock:
            return {
                **self._metrics,
                "alive_workers": sum(1 for t in self._threads if t.is_alive()),
            }

    def _slot_loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._run_slot_generation_once()
            except Exception as exc:
                LOGGER.exception("Slot automation run failed: %s", exc)
                self._inc_metric("slot_errors", 1)
            elapsed = max(0.0, time.time() - started)
            sleep_for = max(1.0, self._slot_generation_interval_seconds - elapsed)
            self._stop.wait(sleep_for)

    def _run_slot_generation_once(self) -> None:
        if not self._scheduling_repository:
            return
        self._scheduling_repository.ensure_rebuild_queue_schema()
        self._run_schedule_rebuild_queue_once()
        schedule_ids = self._scheduling_repository.list_active_schedule_ids(
            days_ahead=self._slot_generation_days_ahead
        )
        generated = 0
        for schedule_id in schedule_ids:
            if self._stop.is_set():
                break
            try:
                self._scheduling_repository.generate_slots_for_schedule(
                    schedule_id=schedule_id,
                    days_ahead=self._slot_generation_days_ahead,
                )
                generated += 1
            except Exception as exc:
                LOGGER.warning(
                    "Slot generation failed for schedule_id=%s days_ahead=%s error=%s",
                    schedule_id,
                    self._slot_generation_days_ahead,
                    exc,
                )
                self._inc_metric("slot_errors", 1)
        self._inc_metric("slot_runs", 1)
        self._inc_metric("slot_generated_for_schedules", generated)
        LOGGER.info(
            "Slot automation run completed schedules=%d generated=%d days_ahead=%d",
            len(schedule_ids),
            generated,
            self._slot_generation_days_ahead,
        )

    def _run_schedule_rebuild_queue_once(self) -> None:
        if not self._scheduling_repository:
            return
        requests = self._scheduling_repository.list_pending_schedule_rebuilds(limit=100)
        processed = 0
        deleted = 0
        for request in requests:
            if self._stop.is_set():
                break
            try:
                dropped = self._scheduling_repository.cleanup_future_available_slots(request.schedule_id)
                self._scheduling_repository.generate_slots_for_schedule(
                    schedule_id=request.schedule_id,
                    days_ahead=self._slot_generation_days_ahead,
                )
                self._scheduling_repository.clear_schedule_rebuild_request(request.schedule_id)
                processed += 1
                deleted += dropped
            except Exception as exc:
                LOGGER.warning(
                    "Schedule rebuild failed schedule_id=%s error=%s",
                    request.schedule_id,
                    exc,
                )
                self._inc_metric("slot_errors", 1)
        self._inc_metric("slot_rebuild_runs", 1)
        self._inc_metric("slot_rebuild_processed", processed)
        self._inc_metric("slot_rebuild_deleted", deleted)
        if requests:
            LOGGER.info(
                "Schedule rebuild queue processed queued=%d rebuilt=%d deleted_available=%d",
                len(requests),
                processed,
                deleted,
            )

    def _reminder_loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._run_reminders_once()
            except Exception as exc:
                LOGGER.exception("Doctor reminder run failed: %s", exc)
                self._inc_metric("reminder_errors", 1)
            elapsed = max(0.0, time.time() - started)
            sleep_for = max(1.0, self._doctor_reminder_interval_seconds - elapsed)
            self._stop.wait(sleep_for)

    def _run_reminders_once(self) -> None:
        if not self._booking_repository:
            return
        due_rows = self._booking_repository.list_due_doctor_reminders(
            lookahead_minutes=max(120, self._doctor_reminder_lead_minutes + 120),
        )
        sent = 0
        skipped = 0
        now = datetime.now()
        grouped: dict[tuple[str, str, int, str, str], list] = {}
        for row in due_rows:
            to_number = self._normalize_whatsapp_number(row.doctor_whatsapp)
            if not to_number:
                skipped += 1
                continue
            if self._source_whatsapp_number and to_number == self._source_whatsapp_number:
                skipped += 1
                continue
            try:
                window_start = datetime.strptime(
                    f"{row.slot_date} {row.schedule_start_time}",
                    "%Y-%m-%d %H:%M",
                )
            except Exception:
                skipped += 1
                continue
            delta_seconds = int((window_start - now).total_seconds())
            center_seconds = self._doctor_reminder_lead_minutes * 60
            if not (center_seconds - self._doctor_reminder_window_seconds <= delta_seconds <= center_seconds + self._doctor_reminder_window_seconds):
                continue
            key = (
                to_number,
                row.slot_date,
                row.schedule_id,
                row.schedule_start_time,
                row.schedule_end_time,
            )
            grouped.setdefault(key, []).append(row)

        for (to_number, slot_date, schedule_id, start_time, end_time), rows in grouped.items():
            dedup_key = f"doctor-schedule-reminder:{to_number}:{slot_date}:{schedule_id}"
            if self._reminder_keys.has(dedup_key):
                skipped += 1
                continue
            rows_sorted = sorted(rows, key=lambda r: (r.slot_time, r.appointment_id))
            lines = [
                f"Reminder: Upcoming appointments in {self._doctor_reminder_lead_minutes} minutes.",
                f"Slot window: {slot_date} {start_time}-{end_time}",
                f"Total patients: {len(rows_sorted)}",
                "Patient list:",
            ]
            for idx, row in enumerate(rows_sorted, start=1):
                lines.append(f"{idx}. {row.slot_time} | {row.patient_name or '-'} | {row.clinic_name or '-'}")
            text = "\n".join(lines)
            try:
                self._send_message_fn(to_number, text)
                self._reminder_keys.add(dedup_key)
                sent += 1
            except Exception as exc:
                LOGGER.warning(
                    "Doctor schedule reminder send failed to=%s date=%s schedule_id=%s error=%s",
                    to_number,
                    slot_date,
                    schedule_id,
                    exc,
                )
                self._inc_metric("reminder_errors", 1)
        self._inc_metric("reminder_runs", 1)
        self._inc_metric("reminder_sent", sent)
        self._inc_metric("reminder_skipped", skipped)
        LOGGER.info(
            "Doctor reminder run completed due=%d sent=%d skipped=%d lead=%dmin",
            len(due_rows),
            sent,
            skipped,
            self._doctor_reminder_lead_minutes,
        )

    def _inc_metric(self, name: str, value: int) -> None:
        with self._metrics_lock:
            self._metrics[name] = int(self._metrics.get(name, 0)) + int(value)

    @staticmethod
    def _normalize_whatsapp_number(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        if raw.startswith("whatsapp:"):
            payload = raw[len("whatsapp:") :].strip()
        else:
            payload = raw
        if payload.startswith("+"):
            return f"whatsapp:{payload}"
        digits = "".join(ch for ch in payload if ch.isdigit())
        if not digits:
            return ""
        if len(digits) == 10:
            digits = f"91{digits}"
        return f"whatsapp:+{digits}"
