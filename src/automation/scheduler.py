import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

from openpyxl import Workbook
from openpyxl.styles import Font

from src.repositories.booking_repository import BookingRepository


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
        send_message_fn: Callable[[str, str], object],
        send_document_fn: Optional[Callable[[str, str, str], None]] = None,
        source_whatsapp_number: str = "",
        enabled: bool = True,
        doctor_reminder_enabled: bool = True,
        doctor_reminder_interval_seconds: int = 60,
        doctor_reminder_lead_minutes: int = 10,
        doctor_reminder_window_seconds: int = 30,
    ) -> None:
        self._booking_repository = booking_repository
        self._send_message_fn = send_message_fn
        self._send_document_fn = send_document_fn
        self._source_whatsapp_number = self._normalize_whatsapp_number(source_whatsapp_number)
        self._enabled = enabled
        self._doctor_reminder_enabled = doctor_reminder_enabled
        self._doctor_reminder_interval_seconds = max(30, int(doctor_reminder_interval_seconds))
        self._doctor_reminder_lead_minutes = max(1, int(doctor_reminder_lead_minutes))
        self._doctor_reminder_window_seconds = max(5, int(doctor_reminder_window_seconds))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_id = f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._metrics_lock = threading.Lock()
        self._metrics = {
            "reminder_runs": 0,
            "reminder_errors": 0,
            "reminder_sent": 0,
            "reminder_skipped": 0,
            "event_runs": 0,
            "event_sent": 0,
            "event_failed": 0,
        }
        self._reminder_keys = _PersistentKeyStore(
            path=os.path.join("data", "doctor_reminder_keys.jsonl"),
            max_entries=200000,
        )

    def start(self) -> None:
        if not self._enabled or self._threads:
            return
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
        grouped: dict[tuple[str, int, str, str], list] = {}
        group_destinations: dict[tuple[str, int, str, str], set[str]] = {}
        for row in due_rows:
            wa_number = self._normalize_whatsapp_number(row.doctor_whatsapp)
            tg_chat_id = self._normalize_telegram_chat_id(row.doctor_telegram_chat_id)
            destinations: list[str] = []
            if wa_number:
                destinations.append(wa_number)
            if tg_chat_id:
                destinations.append(f"telegram:{tg_chat_id}")
            if self._source_whatsapp_number and wa_number and wa_number == self._source_whatsapp_number:
                destinations = [d for d in destinations if d != wa_number]
            if not destinations:
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
                row.slot_date,
                row.schedule_id,
                row.schedule_start_time,
                row.schedule_end_time,
            )
            grouped.setdefault(key, []).append(row)
            group_destinations.setdefault(key, set()).update(destinations)

        for (slot_date, schedule_id, start_time, end_time), rows in grouped.items():
            dedup_key = f"doctor-schedule-reminder:{slot_date}:{schedule_id}:{start_time}:{end_time}"
            if self._reminder_keys.has(dedup_key):
                skipped += 1
                continue
            destinations = sorted(group_destinations.get((slot_date, schedule_id, start_time, end_time), set()))
            if not destinations:
                skipped += 1
                continue
            rows_sorted = sorted(rows, key=lambda r: (r.slot_time, r.appointment_id))
            summary_lines = [
                f"Reminder: Upcoming appointments in {self._doctor_reminder_lead_minutes} minutes.",
                f"Slot window: {slot_date} {self._format_display_time(start_time)}-{self._format_display_time(end_time)}",
                f"Total patients: {len(rows_sorted)}",
            ]
            summary_text = "\n".join(summary_lines)
            any_sent = False
            for to_number in destinations:
                try:
                    report_path = self._build_doctor_report_xlsx(
                        rows=rows_sorted,
                        to_number=to_number,
                        slot_date=slot_date,
                        schedule_id=schedule_id,
                        start_time=start_time,
                        end_time=end_time,
                    )
                    if self._send_document_fn:
                        self._send_document_fn(to_number, report_path, summary_text)
                    else:
                        self._send_message_fn(
                            to_number,
                            summary_text + "\nReport generated: " + os.path.basename(report_path),
                        )
                    any_sent = True
                except Exception as exc:
                    LOGGER.warning(
                        "Doctor schedule reminder send failed to=%s date=%s schedule_id=%s error=%s",
                        to_number,
                        slot_date,
                        schedule_id,
                        exc,
                    )
                    self._inc_metric("reminder_errors", 1)
            if any_sent:
                self._reminder_keys.add(dedup_key)
                sent += 1
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
        self._run_event_notifications_once()

    def _run_event_notifications_once(self) -> None:
        if not self._booking_repository:
            return
        events = self._booking_repository.claim_pending_notification_events(
            limit=200,
            worker_id=self._worker_id,
        )
        sent = 0
        failed = 0
        max_attempts = 5
        for event in events:
            try:
                to_number = self._notification_destination(event)
                if not to_number:
                    self._booking_repository.mark_notification_event_retry(
                        notification_id=event.notification_id,
                        error_text="No patient destination (phone/chat id) available.",
                        backoff_seconds=120,
                        max_attempts=max_attempts,
                    )
                    failed += 1
                    continue

                text = self._event_message_text(event)
                provider_sid = self._send_message_fn(to_number, text)
                self._booking_repository.mark_notification_event_status(
                    notification_id=event.notification_id,
                    status="SENT",
                    provider_message_sid=str(provider_sid or ""),
                )
                sent += 1
            except Exception as exc:
                backoff = min(1800, 60 * (2 ** max(0, int(event.attempt_count))))
                self._booking_repository.mark_notification_event_retry(
                    notification_id=event.notification_id,
                    error_text=str(exc),
                    backoff_seconds=backoff,
                    max_attempts=max_attempts,
                )
                failed += 1
        self._inc_metric("event_runs", 1)
        self._inc_metric("event_sent", sent)
        self._inc_metric("event_failed", failed)
        if events:
            LOGGER.info(
                "Event notifications processed queued=%d sent=%d failed=%d",
                len(events),
                sent,
                failed,
            )

    def _notification_destination(self, event) -> str:
        destination = (event.destination or "").strip()
        channel = (event.channel or "").strip().lower()
        if destination:
            if destination.startswith("telegram:") or destination.startswith("whatsapp:"):
                return destination
            if channel == "telegram":
                return f"telegram:{destination}"
            if channel == "whatsapp":
                return self._normalize_whatsapp_number(destination)

        chat_id = self._normalize_telegram_chat_id(event.patient_telegram_chat_id or "")
        phone = self._normalize_whatsapp_number(event.patient_phone or "")
        # Channel-aware fallback:
        # - Telegram events should route to Telegram chat IDs.
        # - WhatsApp events should route to phone numbers.
        # - Auto/unknown channel prefers Telegram when chat ID exists.
        if channel == "telegram":
            if chat_id:
                return f"telegram:{chat_id}"
            if phone:
                return phone
            return ""
        if channel == "whatsapp":
            if phone:
                return phone
            if chat_id:
                return f"telegram:{chat_id}"
            return ""
        if chat_id:
            return f"telegram:{chat_id}"
        if phone:
            return phone
        return ""

    def _event_message_text(self, event) -> str:
        when = f"{event.slot_date} {self._format_display_time(event.slot_time)}".strip()
        clinic = event.clinic_name or "the clinic"
        event_type = (event.event_type or "").strip().upper()
        if event_type == "CANCELLED":
            return (
                f"Update: Your appointment at {clinic} on {when} was cancelled by the doctor. "
                "Please book another slot."
            )
        if event_type == "RESCHEDULED":
            return (
                f"Update: Your appointment at {clinic} has been rescheduled. "
                f"Current slot: {when}."
            )
        if event_type == "DOCTOR_DELAYED":
            delay_text = ""
            meta = (event.meta_json or "").strip()
            if meta:
                try:
                    payload = json.loads(meta)
                    mins = payload.get("delay_minutes")
                    if mins is not None:
                        delay_text = f" Doctor delay: {mins} minutes."
                except Exception:
                    delay_text = ""
            return (
                f"Update: Doctor is running late for your appointment at {clinic} on {when}."
                f"{delay_text}"
            )
        return f"Appointment update for {clinic} on {when}."

    def _build_doctor_report_xlsx(
        self,
        *,
        rows: list,
        to_number: str,
        slot_date: str,
        schedule_id: int,
        start_time: str,
        end_time: str,
    ) -> str:
        os.makedirs(os.path.join("data", "reports"), exist_ok=True)
        safe_to = "".join(ch for ch in to_number if ch.isalnum() or ch in {"-", "_"})
        filename = (
            f"doctor_reminder_{slot_date}_{schedule_id}_"
            f"{start_time.replace(':', '')}_{end_time.replace(':', '')}_{safe_to}.xlsx"
        )
        path = os.path.join("data", "reports", filename)

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Appointments"

        headers = [
            "Booking Number",
            "Patient Name",
            "Contact",
            "Clinic",
            "Appointment Date",
            "Appointment Time",
            "Status",
        ]
        sheet.append(headers)
        for idx in range(1, len(headers) + 1):
            sheet.cell(row=1, column=idx).font = Font(bold=True)

        for row in rows:
            booking_number = row.booking_number if row.booking_number is not None else row.appointment_id
            sheet.append(
                [
                    booking_number,
                    row.patient_name or "-",
                    row.patient_contact or "-",
                    row.clinic_name or "-",
                    row.slot_date or "-",
                    self._format_display_time(row.slot_time),
                    row.status or "-",
                ]
            )

        widths = {
            "A": 16,
            "B": 28,
            "C": 18,
            "D": 32,
            "E": 18,
            "F": 18,
            "G": 14,
        }
        for col, width in widths.items():
            sheet.column_dimensions[col].width = width

        workbook.save(path)
        return path

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

    @staticmethod
    def _normalize_telegram_chat_id(value: str) -> str:
        raw = (value or "").strip()
        if raw.startswith("telegram:"):
            raw = raw[len("telegram:") :].strip()
        return raw

    @staticmethod
    def _format_display_time(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return text
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return datetime.strptime(text, fmt).strftime("%I:%M %p")
            except ValueError:
                continue
        return text
