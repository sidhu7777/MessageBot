import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Optional

from openpyxl import Workbook
from openpyxl.styles import Font

from src.repositories.booking_repository import BookingRepository
from src.repositories.notification_repository import NotificationEvent
from src.runtime.account_scope import build_scoped_user_id
from src.runtime.kafka_notification_bridge import KafkaNotificationBridge


LOGGER = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


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
        settings: Optional[Any] = None,
        booking_repository: Optional[BookingRepository],
        send_message_fn: Callable[[str, str], object],
        send_document_fn: Optional[Callable[[str, str, str], None]] = None,
        source_whatsapp_number: str = "",
        enabled: bool = True,
        doctor_reminder_enabled: bool = True,
        doctor_reminder_interval_seconds: int = 60,
        doctor_reminder_lead_minutes: int = 10,
        doctor_reminder_window_seconds: int = 30,
        doctor_reminder_lead_minutes_list: Optional[list] = None,
        resolve_channel_account_id_fn: Optional[Callable[[str, int], Optional[int]]] = None,
        notification_bridge: Optional[KafkaNotificationBridge] = None,
    ) -> None:
        self._settings = settings
        self._booking_repository = booking_repository
        self._send_message_fn = send_message_fn
        self._send_document_fn = send_document_fn
        self._source_whatsapp_number = self._normalize_whatsapp_number(source_whatsapp_number)
        self._enabled = enabled
        self._doctor_reminder_enabled = doctor_reminder_enabled
        self._doctor_reminder_interval_seconds = max(30, int(doctor_reminder_interval_seconds))
        self._doctor_reminder_lead_minutes = max(1, int(doctor_reminder_lead_minutes))
        self._doctor_reminder_window_seconds = max(5, int(doctor_reminder_window_seconds))
        # Two-send list: default [60, 10] → send at T-60 (1 hour before) and T-10 (10 min before)
        if doctor_reminder_lead_minutes_list is not None:
            self._doctor_reminder_lead_minutes_list = [max(1, int(m)) for m in doctor_reminder_lead_minutes_list]
        else:
            lead = self._doctor_reminder_lead_minutes
            self._doctor_reminder_lead_minutes_list = [60, lead] if lead != 60 else [60]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_id = f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._resolve_channel_account_id_fn = resolve_channel_account_id_fn
        self._notification_bridge = notification_bridge
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
        self._report_retention_days = max(1, int(os.getenv("REPORT_RETENTION_DAYS", "14")))
        self._report_max_files = max(50, int(os.getenv("REPORT_MAX_FILES", "5000")))
        self._report_cleanup_interval_seconds = max(
            300, int(os.getenv("REPORT_CLEANUP_INTERVAL_SECONDS", "3600"))
        )
        self._next_report_cleanup_at = 0.0

    def start(self) -> None:
        if not self._enabled or self._threads:
            return
        if self._notification_bridge:
            self._notification_bridge.start()
        if self._doctor_reminder_enabled and self._booking_repository:
            thread = threading.Thread(target=self._reminder_loop, name="doctor-reminder", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        if self._notification_bridge:
            self._notification_bridge.stop()

    def snapshot(self) -> dict:
        with self._metrics_lock:
            return {
                **self._metrics,
                "alive_workers": sum(1 for t in self._threads if t.is_alive()),
                "notification_bridge": self._notification_bridge.snapshot() if self._notification_bridge else {},
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

        # Lookahead must cover the largest lead time + safety buffer
        max_lead = max(self._doctor_reminder_lead_minutes_list)
        due_rows = self._booking_repository.list_due_doctor_reminders(
            lookahead_minutes=max(240, max_lead + 120),
        )

        # ── Bulk-fetch extra contacts from doctor_whatsapp_numbers ────────────
        _all_doctor_ids = list({r.doctor_id for r in due_rows if r.doctor_id})
        _extra_contacts: dict[int, list[dict]] = {}
        if _all_doctor_ids:
            try:
                _extra_contacts = self._booking_repository.get_extra_doctor_contacts(
                    _all_doctor_ids
                )
            except Exception as exc:
                LOGGER.warning("get_extra_doctor_contacts failed: %s", exc)

        sent = 0
        skipped = 0
        now = datetime.now(IST)

        # ── Loop over each configured lead time (e.g. 60 min and 10 min) ─────
        for lead_minutes in self._doctor_reminder_lead_minutes_list:
            center_seconds = lead_minutes * 60

            # Group rows whose T-minus window matches this lead time
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
                # ── Merge extra contacts from doctor_whatsapp_numbers ─────────
                for _extra in _extra_contacts.get(row.doctor_id, []):
                    _ewa = self._normalize_whatsapp_number(str(_extra.get("whatsapp") or ""))
                    _etg = self._normalize_telegram_chat_id(str(_extra.get("telegram") or ""))
                    if _ewa and _ewa not in destinations and _ewa != self._source_whatsapp_number:
                        destinations.append(_ewa)
                    if _etg and f"telegram:{_etg}" not in destinations:
                        destinations.append(f"telegram:{_etg}")
                if not destinations:
                    skipped += 1
                    continue
                try:
                    window_start = datetime.strptime(
                        f"{row.slot_date} {row.schedule_start_time}",
                        "%Y-%m-%d %H:%M",
                    ).replace(tzinfo=IST)
                except Exception:
                    skipped += 1
                    continue
                delta_seconds = int((window_start - now).total_seconds())
                if not (
                    center_seconds - self._doctor_reminder_window_seconds
                    <= delta_seconds
                    <= center_seconds + self._doctor_reminder_window_seconds
                ):
                    continue
                key = (row.slot_date, row.schedule_id, row.schedule_start_time, row.schedule_end_time)
                grouped.setdefault(key, []).append(row)
                group_destinations.setdefault(key, set()).update(destinations)

            # ── Send one xlsx per schedule group ─────────────────────────────
            for (slot_date, schedule_id, start_time, end_time), rows in grouped.items():
                destinations = sorted(
                    group_destinations.get((slot_date, schedule_id, start_time, end_time), set())
                )
                if not destinations:
                    skipped += 1
                    continue

                rows_sorted = sorted(rows, key=lambda r: (r.slot_time, r.appointment_id))
                summary_lines = [
                    f"Reminder: Upcoming appointments in {lead_minutes} minutes.",
                    f"Slot window: {slot_date} {self._format_display_time(start_time)}-{self._format_display_time(end_time)}",
                    f"Total patients: {len(rows_sorted)}",
                ]
                summary_text = "\n".join(summary_lines)

                any_sent = False
                for to_number in destinations:
                    channel = "telegram" if to_number.startswith("telegram:") else "whatsapp"
                    scoped_to_number = to_number
                    channel_account_id: Optional[int] = None
                    doctor_id = getattr(rows_sorted[0], "doctor_id", None)
                    if (
                        self._resolve_channel_account_id_fn
                        and doctor_id is not None
                        and channel in {"telegram", "whatsapp"}
                    ):
                        try:
                            resolved = self._resolve_channel_account_id_fn(channel, int(doctor_id))
                            if resolved is not None:
                                channel_account_id = int(resolved)
                                scoped_to_number = build_scoped_user_id(channel_account_id, to_number)
                        except Exception:
                            channel_account_id = None
                            scoped_to_number = to_number
                    dedup_key = (
                        f"doctor-schedule-reminder:{slot_date}:{schedule_id}:"
                        f"{start_time}:{end_time}:{lead_minutes}min:{channel}:{to_number}"
                    )

                    # ── Flat-file dedup (fast check, also covers mocked tests) ─
                    if self._reminder_keys.has(dedup_key):
                        skipped += 1
                        continue

                    # ── DB dedup check ────────────────────────────────────────
                    queue_id = None
                    try:
                        sent_flag = self._booking_repository.is_reminder_sent(dedup_key=dedup_key)
                        # Guard: MagicMock in tests returns truthy non-bool — treat as False
                        if not isinstance(sent_flag, bool):
                            sent_flag = False
                        if sent_flag:
                            self._reminder_keys.add(dedup_key)  # sync flat file
                            skipped += 1
                            continue
                        doctor_id = getattr(rows_sorted[0], "doctor_id", None)
                        if doctor_id is None:
                            skipped += 1
                            continue
                        queue_id = self._booking_repository.insert_or_get_reminder_queue(
                            doctor_id=int(doctor_id),
                            schedule_id=schedule_id,
                            slot_date=slot_date,
                            schedule_start_time=start_time,
                            schedule_end_time=end_time,
                            channel=channel,
                            destination=to_number,
                            lead_minutes=lead_minutes,
                            dedup_key=dedup_key,
                        )
                        if not isinstance(queue_id, int):
                            queue_id = None
                    except Exception as exc:
                        LOGGER.warning("doctor_remainder_queue DB check failed: %s", exc)
                        queue_id = None

                    # ── Build report xlsx and send ────────────────────────────
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
                            self._send_document_fn(scoped_to_number, report_path, summary_text)
                        else:
                            self._send_message_fn(
                                scoped_to_number,
                                summary_text + "\nReport generated: " + os.path.basename(report_path),
                            )
                        # ── Mark SENT in DB + flat file ──────────────────────
                        try:
                            if queue_id is not None:
                                self._booking_repository.mark_reminder_sent(queue_id=queue_id)
                        except Exception as dbe:
                            LOGGER.warning("mark_reminder_sent failed queue_id=%s: %s", queue_id, dbe)
                        # Always cache in flat file so dedup is instant next tick
                        self._reminder_keys.add(dedup_key)
                        any_sent = True
                    except Exception as exc:
                        LOGGER.warning(
                            "Doctor schedule reminder send failed to=%s date=%s schedule_id=%s lead=%dmin error=%s",
                            to_number, slot_date, schedule_id, lead_minutes, exc,
                        )
                        try:
                            if queue_id is not None:
                                self._booking_repository.mark_reminder_failed(
                                    queue_id=queue_id, error=str(exc)
                                )
                        except Exception:
                            pass
                        self._inc_metric("reminder_errors", 1)

                if any_sent:
                    sent += 1

        self._inc_metric("reminder_runs", 1)
        self._inc_metric("reminder_sent", sent)
        self._inc_metric("reminder_skipped", skipped)
        LOGGER.info(
            "Doctor reminder run completed due=%d sent=%d skipped=%d leads=%s",
            len(due_rows),
            sent,
            skipped,
            self._doctor_reminder_lead_minutes_list,
        )
        now_monotonic = time.monotonic()
        if now_monotonic >= self._next_report_cleanup_at:
            removed = self._cleanup_report_files()
            if removed:
                LOGGER.info(
                    "Cleaned report files removed=%d retention_days=%d max_files=%d",
                    removed,
                    self._report_retention_days,
                    self._report_max_files,
                )
            self._next_report_cleanup_at = now_monotonic + self._report_cleanup_interval_seconds
        self._run_event_notifications_once()

    def _run_event_notifications_once(self) -> None:
        if not self._booking_repository:
            return
        events = self._booking_repository.claim_pending_notification_events(
            limit=200,
            worker_id=self._worker_id,
        )
        if not events:
            return
        sent = 0
        failed = 0
        queued = 0
        if self._notification_bridge:
            queued, sent, failed = self._notification_bridge.process_pending_events(events)
        else:
            for event in events:
                if self._process_notification_event(event):
                    sent += 1
                else:
                    failed += 1
        self._inc_metric("event_runs", 1)
        self._inc_metric("event_sent", sent)
        self._inc_metric("event_failed", failed)
        if events:
            LOGGER.info(
                "Event notifications processed queued=%d sent=%d failed=%d kafka_enqueued=%d",
                len(events),
                sent,
                failed,
                queued,
            )

    def _process_notification_event(self, event: NotificationEvent) -> bool:
        if not self._booking_repository:
            return False
        max_attempts = 5
        try:
            to_number = self._notification_destination(event)
            if not to_number:
                self._booking_repository.mark_notification_event_retry(
                    notification_id=event.notification_id,
                    error_text="No patient destination (phone/chat id) available.",
                    backoff_seconds=120,
                    max_attempts=max_attempts,
                )
                return False

            channel = (event.channel or "").strip().lower()
            doctor_id = int(event.doctor_id) if getattr(event, "doctor_id", None) is not None else None
            channel_account_id: Optional[int] = None

            # DEBUG: Log the channel value and event details
            LOGGER.info(f"DEBUG: Processing notification event - channel='{channel}' appointment_id={event.appointment_id} event_type={event.event_type} destination={event.destination}")

            # Handle SMS channel separately — ONLY use SMS service, never fall back to Twilio
            if channel == "sms":
                LOGGER.info(f"DEBUG: SMS channel detected - using SMSNotificationService for appointment_id={event.appointment_id}")
                from src.runtime.sms_notification_service import SMSNotificationService

                sms_service = SMSNotificationService(self._settings, LOGGER)
                
                # Extract source_channel from meta_json (it's stored as JSON, not as an attribute)
                source_channel = ""
                try:
                    import json
                    meta = json.loads(event.meta_json or "{}")
                    source_channel = str(meta.get("source_channel", "")).strip().lower()
                except Exception:
                    pass
                
                sms_allowed = (
                    sms_service.is_sms_enabled_for_channel(source_channel)
                    if source_channel
                    else sms_service.sms_enabled
                )
                
                if not sms_allowed:
                    self._mark_notification_event_status(
                        notification_id=event.notification_id,
                        status="SKIPPED",
                        error_text=(
                            f"SMS disabled for source channel '{source_channel or 'unknown'}'."
                        ),
                        doctor_id=doctor_id,
                        admin_id=event.admin_id,
                    )
                    return True

                # Build message based on event type
                message = self._build_sms_message(event)
                if not message:
                    backoff = min(1800, 60 * (2 ** max(0, int(event.attempt_count))))
                    self._booking_repository.mark_notification_event_retry(
                        notification_id=event.notification_id,
                        error_text="Could not build SMS message",
                        backoff_seconds=backoff,
                        max_attempts=max_attempts,
                    )
                    return False

                # Send SMS via SMS service with credit check
                try:
                    # Use new method with credit check
                    success, provider_sid, failure_reason = sms_service.send_sms_with_credit_check(
                        doctor_id=doctor_id,
                        appointment_id=event.appointment_id,
                        phone_number=to_number,
                        message=message,
                    )

                    if success:
                        self._mark_notification_event_status(
                            notification_id=event.notification_id,
                            status="SENT",
                            provider_message_sid=str(provider_sid or ""),
                            doctor_id=doctor_id,
                            admin_id=event.admin_id,
                        )
                        return True
                    else:
                        # SMS service returned False - mark for retry with actual reason
                        error_text = f"SMS send failed: {failure_reason}" if failure_reason else "SMS API returned failure"
                        
                        # Don't retry if credits exhausted, service disabled, or SMS service unavailable
                        if failure_reason in ("CREDITS_EXHAUSTED", "SERVICE_DISABLED", "SMS_SERVICE_UNAVAILABLE"):
                            self._mark_notification_event_status(
                                notification_id=event.notification_id,
                                status="FAILED",
                                error_text=error_text,
                                doctor_id=doctor_id,
                                admin_id=event.admin_id,
                            )
                            return True  # Don't retry
                        
                        # Retry for other failures (API_TIMEOUT, API_ERROR, SMS_SEND_FAILED, etc.)
                        backoff = min(1800, 60 * (2 ** max(0, int(event.attempt_count))))
                        self._booking_repository.mark_notification_event_retry(
                            notification_id=event.notification_id,
                            error_text=error_text,
                            backoff_seconds=backoff,
                            max_attempts=max_attempts,
                        )
                        return False
                except Exception as exc:
                    # SMS service threw exception - mark for retry with actual error
                    backoff = min(1800, 60 * (2 ** max(0, int(event.attempt_count))))
                    self._booking_repository.mark_notification_event_retry(
                        notification_id=event.notification_id,
                        error_text=f"SMS send failed: {str(exc)}",
                        backoff_seconds=backoff,
                        max_attempts=max_attempts,
                    )
                    return False

            # Handle Telegram/WhatsApp/other channels via send_message_fn
            LOGGER.info(f"DEBUG: Non-SMS channel detected - using send_message_fn for channel='{channel}' appointment_id={event.appointment_id}")
            if (
                self._resolve_channel_account_id_fn
                and doctor_id is not None
                and channel in {"telegram", "whatsapp"}
            ):
                try:
                    resolved = self._resolve_channel_account_id_fn(channel, doctor_id)
                    if resolved is not None:
                        channel_account_id = int(resolved)
                        to_number = build_scoped_user_id(channel_account_id, to_number)
                except Exception:
                    channel_account_id = None

            text = self._event_message_text(event)
            provider_sid = self._send_message_fn(to_number, text)
            self._mark_notification_event_status(
                notification_id=event.notification_id,
                status="SENT",
                provider_message_sid=str(provider_sid or ""),
                channel_account_id=channel_account_id,
                doctor_id=doctor_id,
                admin_id=event.admin_id,
            )
            return True
        except Exception as exc:
            backoff = min(1800, 60 * (2 ** max(0, int(event.attempt_count))))
            self._booking_repository.mark_notification_event_retry(
                notification_id=event.notification_id,
                error_text=str(exc),
                backoff_seconds=backoff,
                max_attempts=max_attempts,
            )
            return False

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
            return destination

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

    def _build_sms_message(self, event: NotificationEvent) -> str:
        """
        Build SMS message for notification event using SMSNotificationService.

        Args:
            event: NotificationEvent with appointment details

        Returns:
            Formatted SMS message text
        """
        try:
            from src.runtime.sms_notification_service import SMSNotificationService

            sms_service = SMSNotificationService(self._settings, LOGGER)
            patient_name = event.patient_name or "Valued Patient"
            doctor_name = getattr(event, "doctor_name", "") or "Doctor"
            appointment_date = event.slot_date or ""
            appointment_time = event.slot_time or ""
            clinic_name = event.clinic_name or "the clinic"

            message = sms_service.build_message_by_event_type(
                event_type=event.event_type,
                patient_name=patient_name,
                doctor_name=doctor_name,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                clinic_name=clinic_name,
            )
            return message
        except Exception as exc:
            LOGGER.error("Failed to build SMS message: %s", exc)
            return ""

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

    def _cleanup_report_files(self) -> int:
        reports_dir = os.path.join("data", "reports")
        if not os.path.isdir(reports_dir):
            return 0
        now_ts = time.time()
        cutoff_ts = now_ts - (self._report_retention_days * 86400)
        files: list[tuple[str, float]] = []
        for name in os.listdir(reports_dir):
            full_path = os.path.join(reports_dir, name)
            if not os.path.isfile(full_path):
                continue
            try:
                mtime = os.path.getmtime(full_path)
            except OSError:
                continue
            files.append((full_path, float(mtime)))

        to_remove: set[str] = {path for path, mtime in files if mtime < cutoff_ts}
        remaining = [(path, mtime) for path, mtime in files if path not in to_remove]
        if len(remaining) > self._report_max_files:
            overflow = len(remaining) - self._report_max_files
            remaining.sort(key=lambda item: item[1])  # oldest first
            for path, _ in remaining[:overflow]:
                to_remove.add(path)

        removed = 0
        for path in to_remove:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        return removed

    def _inc_metric(self, name: str, value: int) -> None:
        with self._metrics_lock:
            self._metrics[name] = int(self._metrics.get(name, 0)) + int(value)

    def _mark_notification_event_status(self, **kwargs) -> None:
        if not self._booking_repository:
            return
        try:
            self._booking_repository.mark_notification_event_status(**kwargs)
        except TypeError:
            fallback_keys = ("notification_id", "status", "error_text", "provider_message_sid")
            fallback_kwargs = {key: kwargs[key] for key in fallback_keys if key in kwargs}
            self._booking_repository.mark_notification_event_status(**fallback_kwargs)

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
