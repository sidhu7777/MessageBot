# Automation Subsystem

`src/automation` implements `AutomationScheduler` — a single in-process, thread-based scheduler that owns two responsibilities for the clinic-appointment-bot: sending doctors periodic pre-appointment patient-list reports ("doctor reminders") ahead of each scheduled slot window, and draining the `appointment_notification_log` table to deliver patient-facing notifications (booking confirmations, cancellations, reschedules, doctor-delay alerts) over WhatsApp, Telegram, or SMS. It also opportunistically prunes old generated `.xlsx` report files. The module is deliberately dependency-injected (repository, send functions, settings) so it can run against a real MySQL-backed `BookingRepository` in production or against mocks/fakes in tests. It is constructed once in `main.py` as the module-level `automation_scheduler` and started/stopped alongside the FastAPI app lifespan.

Back to [root README](../../README.md).

## Files

- `scheduler.py` — the entire subsystem: `AutomationScheduler` and its private helper `_PersistentKeyStore`.
- `__init__.py` — re-exports `AutomationScheduler` as the package's public surface (`from src.automation import AutomationScheduler`).

Two *other* background loops — overflow-turn polling and doctor-cache-invalidation polling — live outside this package in `src/runtime/background_workers.py`. They are not part of `AutomationScheduler` and are documented separately in [src/runtime/README.md](../runtime/README.md).

## Threading model at a glance

`AutomationScheduler.start()` spawns exactly **one** daemon thread, named `"doctor-reminder"`, running `_reminder_loop`. There is no separate thread for event notifications — `_run_event_notifications_once()` (event-notification processing) is called synchronously at the *end* of every `_run_reminders_once()` tick, and report-file cleanup (`_cleanup_report_files()`) is also triggered opportunistically from inside that same tick. So a single loop, on a single thread, does three things in sequence each cycle: reminders, then (throttled) report cleanup, then event notifications.

If `notification_bridge` (a `KafkaNotificationBridge`) is configured, it is started/stopped alongside the scheduler in `start()`/`stop()`, but it does not add its own thread inside `AutomationScheduler` — it runs its own consumer internally and is simply invoked from `_run_event_notifications_once()`.

## 1. Doctor reminder loop

### Loop mechanics

- `start()` launches `_reminder_loop` as thread name `"doctor-reminder"` only if `self._enabled`, `self._doctor_reminder_enabled`, and `self._booking_repository` are all truthy.
- `_reminder_loop()` runs until `self._stop` (a `threading.Event`) is set. Each iteration calls `_run_reminders_once()`, catches and logs any exception (incrementing the `reminder_errors` metric), then sleeps for `max(1.0, DOCTOR_REMINDER_INTERVAL_SECONDS - elapsed)` via `self._stop.wait(...)` so `stop()` can interrupt the sleep immediately.
- The interval is `self._doctor_reminder_interval_seconds`, clamped to a minimum of 5 seconds, default 60 (`DOCTOR_REMINDER_INTERVAL_SECONDS`).

### Finding due appointments

`_run_reminders_once()` computes `max_lead = max(self._doctor_reminder_lead_minutes_list)` and calls:

```python
due_rows = self._booking_repository.list_due_doctor_reminders(
    lookahead_minutes=max(240, max_lead + 120),
)
```

So the lookahead window is always at least 240 minutes, or `max_lead + 120` minutes if that's larger — enough slack that appointments due at the far-future lead time (e.g. 60 minutes out) are visible well before they enter their actual send window.

It then bulk-fetches "extra" doctor contacts (additional WhatsApp/Telegram numbers configured per doctor beyond the primary one) in a single call: `self._booking_repository.get_extra_doctor_contacts(_all_doctor_ids)`, keyed by `doctor_id`, wrapped in a try/except so a failure there doesn't abort the whole run.

### Lead times and grouping

`self._doctor_reminder_lead_minutes_list` defaults to `[60, lead]` where `lead` is `doctor_reminder_lead_minutes` (default 10) — i.e. `[60, 10]` out of the box — unless `doctor_reminder_lead_minutes_list` is passed explicitly to the constructor (or, via `main.py`, parsed from the `DOCTOR_REMINDER_LEAD_MINUTES_LIST` env var, comma-separated). If `lead == 60` the list collapses to just `[60]`.

For **each lead time** in that list, the scheduler:

1. Converts `lead_minutes` to `center_seconds = lead_minutes * 60`.
2. Iterates every `due_rows` row, building a `destinations` list per row:
   - The doctor's primary WhatsApp number (normalized via `_normalize_whatsapp_number`), unless it equals `source_whatsapp_number` (the bot's own sending number — avoids self-notifying).
   - The doctor's Telegram chat id (normalized via `_normalize_telegram_chat_id`, prefixed `telegram:`).
   - Any extra contacts from `get_extra_doctor_contacts`, merged in (deduplicated against what's already in `destinations`).
   - Rows with no resolvable destination are counted as `skipped` and dropped.
3. Parses `f"{row.slot_date} {row.schedule_start_time}"` as `%Y-%m-%d %H:%M` in the `Asia/Kolkata` (`IST`) timezone to get `window_start`. Parse failures are skipped.
4. Computes `delta_seconds = (window_start - now).total_seconds()` and keeps the row only if it falls inside the window `center_seconds ± DOCTOR_REMINDER_WINDOW_SECONDS` (default ±30s). This is what makes the loop fire once per lead time per schedule, even though it runs every 60s — the row is only "in window" for a ~60-second band around each configured lead time.
5. Rows passing the window check are grouped by the tuple key `(slot_date, schedule_id, schedule_start_time, schedule_end_time)` — i.e. one group per clinic schedule/slot-window, aggregating all patients booked in that window. Destinations across the group's rows are unioned into `group_destinations`.

### Per-group send: one xlsx per destination

For each group, rows are sorted by `(slot_time, appointment_id)`. A summary text is built:

```
Reminder: Upcoming appointments in {lead_minutes} minutes.
Slot window: {slot_date} {start_time}-{end_time}
Total patients: {N}
```

Then for **every destination** in that group (WhatsApp number or `telegram:<chat_id>`):

- `channel` is inferred from the destination prefix (`"telegram"` if it starts with `telegram:`, else `"whatsapp"`).
- If `resolve_channel_account_id_fn` is provided and the doctor id is known, it's called as `resolve_channel_account_id_fn(channel, doctor_id)` to look up a multi-tenant/multi-account routing id; when resolved, the destination is rewritten via `build_scoped_user_id(channel_account_id, to_number)` (from `src.runtime.account_scope`) so downstream senders route through the correct account.
- A **dedup key** is built as:
  ```
  doctor-schedule-reminder:{slot_date}:{schedule_id}:{start_time}:{end_time}:{lead_minutes}min:{channel}:{to_number}
  ```
  Note the key uses the *unscoped* `to_number`, not the account-scoped version.

#### Double-layered dedup

1. **Flat-file fast path** — `self._reminder_keys` is a `_PersistentKeyStore` backed by `data/doctor_reminder_keys.jsonl`. `self._reminder_keys.has(dedup_key)` is checked first; if present, the send is skipped immediately without touching the database. This makes repeated ticks cheap and also keeps dedup working when the DB is mocked out (e.g. in tests) or briefly unavailable.
2. **DB-backed durable path** — if the flat-file check misses, the scheduler asks the database:
   - `self._booking_repository.is_reminder_sent(dedup_key=dedup_key)` — if true, the flat-file store is back-filled (`self._reminder_keys.add(dedup_key)`) to sync it, and the send is skipped.
   - Otherwise `self._booking_repository.insert_or_get_reminder_queue(doctor_id=..., schedule_id=..., slot_date=..., schedule_start_time=..., schedule_end_time=..., channel=..., destination=to_number, lead_minutes=..., dedup_key=...)` inserts (or fetches an existing) queue row and returns `queue_id`. Any exception here is caught and logged, and `queue_id` stays `None` — the send still proceeds even if this bookkeeping step fails, it just won't be able to mark the DB row afterward.

   After a successful send, `self._booking_repository.mark_reminder_sent(queue_id=queue_id)` is called (only if `queue_id` was obtained), and — regardless — `self._reminder_keys.add(dedup_key)` is always called so the flat file reflects the send. If the send raises, `self._booking_repository.mark_reminder_failed(queue_id=queue_id, error=str(exc))` is called instead (best-effort, wrapped in try/except) and the flat file is **not** updated, so the next tick will retry.

   The flat file therefore acts as an at-least-once-suppressing cache that survives process restarts (it's read from disk in `_PersistentKeyStore.__init__` via `_load()`), while the DB is the source of truth for cross-instance/cross-restart durability and audit history (`reminder_queue`-style status tracking).

`_PersistentKeyStore` itself (`data/doctor_reminder_keys.jsonl`): each line is a JSON object `{"key": ..., "ts": ...}`. On `add()`, the key is appended to the in-memory set/list and the file (append mode); if the in-memory entry count exceeds `max_entries` (200000, set by the scheduler), it's trimmed to the most recent `max_entries` entries and the whole file is rewritten. All access is guarded by a `threading.Lock`.

### Building and sending the xlsx report

`_build_doctor_report_xlsx(rows=..., to_number=..., slot_date=..., schedule_id=..., start_time=..., end_time=...)`:

- Ensures `data/reports/` exists.
- Builds a filename: `doctor_reminder_{slot_date}_{schedule_id}_{start_time_no_colons}_{end_time_no_colons}_{safe_to_number}.xlsx`, where `safe_to_number` strips everything except alphanumerics, `-`, and `_` from `to_number`.
- Uses `openpyxl.Workbook()`, sheet titled `"Appointments"`, with a **bold header row**:

  | Booking Number | Patient Name | Contact | Clinic | Appointment Date | Appointment Time | Status |
  |---|---|---|---|---|---|---|

- One data row per appointment in `rows` (already sorted by slot time/appointment id): `booking_number` (falls back to `appointment_id` if `booking_number` is `None`), `patient_name` (or `"-"`), `patient_contact` (or `"-"`), `clinic_name` (or `"-"`), `slot_date` (or `"-"`), `slot_time` formatted via `_format_display_time` (converts `HH:MM`/`HH:MM:SS`/12-hour formats to `%I:%M %p`), and `status` (or `"-"`).
- Sets fixed column widths (A=16, B=28, C=18, D=32, E=18, F=18, G=14) and saves the workbook to `data/reports/<filename>`, returning that path.

Sending: if `self._send_document_fn` is configured, it's called as `send_document_fn(scoped_to_number, report_path, summary_text)` (document attachment + caption). If not configured, the scheduler falls back to `self._send_message_fn(scoped_to_number, summary_text + "\nReport generated: " + basename)` — a plain text message referencing the file instead of attaching it.

### Metrics from this loop

Each run increments: `reminder_runs` (+1), `reminder_sent` (+1 per group that had at least one successful send — not per destination), `reminder_skipped` (+1 per row/destination that hit any of the skip conditions above), and `reminder_errors` (+1 per failed per-destination send, plus +1 if `_run_reminders_once` itself raises).

## 2. Event-notification loop

`_run_event_notifications_once()` is **not** a separate thread — it is called as the last step of `_run_reminders_once()`, once per reminder-loop tick (so effectively every `DOCTOR_REMINDER_INTERVAL_SECONDS`, i.e. every 60 seconds by default). Confirmed directly from the code: `_run_reminders_once` ends with `self._run_event_notifications_once()`, and `start()` never spawns a thread targeting it.

### Claiming events

```python
events = self._booking_repository.claim_pending_notification_events(
    limit=200,
    worker_id=self._worker_id,
)
```

`self._worker_id` is a per-process unique id (`f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"`) so concurrent workers/instances don't double-claim. The underlying implementation (`src/repositories/notification_ops.py::claim_pending_notification_events`) selects rows from `appointment_notification_log` where `status IN ('PENDING', 'FAILED')`, `dead_at IS NULL`, `next_retry_at` has elapsed (or is null), and the row isn't currently locked by another worker (`locked_at` stale check), using `FOR UPDATE SKIP LOCKED` for safe concurrent claiming — up to 200 rows per tick. These rows are the `NotificationEvent`s for the appointment lifecycle events the system produces: `CONFIRMATION`, `CANCELLED`, `RESCHEDULED`, and `DOCTOR_DELAYED` (event types are stamped in when the row is inserted elsewhere in the booking flow, e.g. `booking_repository.py` inserting `event_type="CONFIRMATION"`/`"DOCTOR_DELAYED"` rows).

If no events are claimed, the method returns immediately (no metrics touched).

### Dispatch: Kafka bridge vs. direct processing

```python
if self._notification_bridge:
    queued, sent, failed = self._notification_bridge.process_pending_events(events)
else:
    for event in events:
        if self._process_notification_event(event):
            sent += 1
        else:
            failed += 1
```

If a `KafkaNotificationBridge` was supplied to the constructor (in `main.py` it's attached post-construction via `automation_scheduler._notification_bridge = KafkaNotificationBridge(settings=..., logger=..., process_event_fn=automation_scheduler._process_notification_event, event_cls=NotificationEvent)`), claimed events are handed off to the bridge, which enqueues them onto Kafka for asynchronous, possibly out-of-process, delivery — but note the bridge is constructed with `process_event_fn=automation_scheduler._process_notification_event`, i.e. the same per-event handler described below still ultimately does the work, just invoked from the Kafka consumer path rather than inline. If no bridge is configured, `_process_notification_event` is called synchronously in this thread for each event.

Metrics updated after either path: `event_runs` (+1 per tick with events present), `event_sent`, `event_failed`.

### `_process_notification_event`: channel branching

For a single `NotificationEvent`, the handler:

1. Resolves the destination via `_notification_destination(event)`:
   - If `event.destination` is already set and prefixed `telegram:`/`whatsapp:`, it's used as-is.
   - Otherwise, if `event.destination` is set but unprefixed, it's coerced based on `event.channel` (`telegram` → prefixed `telegram:`; `whatsapp` → normalized via `_normalize_whatsapp_number`; anything else passed through raw).
   - If `event.destination` is empty, falls back to `event.patient_telegram_chat_id` / `event.patient_phone`, again channel-aware (telegram-channel events prefer chat id then phone; whatsapp-channel events prefer phone then chat id; unknown channel prefers chat id then phone).
   - If nothing resolves, the event is marked for retry via `mark_notification_event_retry(..., error_text="No patient destination (phone/chat id) available.", backoff_seconds=120, max_attempts=5)` and the event is treated as failed for this tick.
2. Normalizes `event.channel` via `_normalize_notification_channel` (lowercased, quote-stripped).

**Branch: `channel == "sms"`** — delegates entirely to `_process_sms_notification_event`, described below. The code comment is explicit that SMS "ONLY use[s] SMS service, never fall back to Twilio [voice/WhatsApp client]."

**Branch: `telegram` / `whatsapp` (or any other non-SMS channel)** — uses the generic `send_message_fn` (referred to in the task as "ChannelDelivery" — in `main.py` this is wired to `_send_plain_channel_message`, which ultimately dispatches over WhatsApp/Telegram depending on the destination prefix):
   - If `resolve_channel_account_id_fn` is set and the doctor id is known and channel is `telegram`/`whatsapp`, it resolves a `channel_account_id` the same way as the reminder loop and rewrites `to_number` via `build_scoped_user_id(channel_account_id, to_number)` — this is the "automatic multi-account routing" mentioned in the task: a clinic/doctor with multiple connected WhatsApp/Telegram accounts gets messages routed through the correct one without the caller needing to know which account owns which patient conversation.
   - Builds message text via `_event_message_text(event)` (per-event-type copy: CANCELLED, RESCHEDULED, DOCTOR_DELAYED with optional delay-minutes parsed from `event.meta_json`, and a generic fallback for anything else, e.g. CONFIRMATION).
   - Calls `provider_sid = self._send_message_fn(to_number, text)`.
   - **SMS-recovery fallback** (the subtle behavior called out in the task): if `send_message_fn` raises, `_should_force_sms_recovery(event=event, to_number=to_number, error=exc)` is checked. This returns `True` only if **all** of the following hold:
     - The event's own channel is `"sms"` (note: this branch is reached only for non-SMS channels in the outer dispatch, but `_should_force_sms_recovery` re-checks `event.channel`, not the branch that was taken — so in practice this recovery only fires when an event nominally tagged `channel="sms"` somehow still reached this generic-sender branch, or more precisely when the *destination* being used is an SMS-style phone number rather than a `telegram:`/`whatsapp:` prefixed one),
     - `to_number` is non-empty and does **not** start with `telegram:` or `whatsapp:` (i.e. it looks like a bare phone number, not a chat/WA destination),
     - the exception text contains the substring `"Twilio client or sender number is not configured"`.
     If all three hold, instead of letting the exception propagate, the handler logs a warning ("Recovered notification through SMS service after generic sender failure") and re-dispatches the same event through `_process_sms_notification_event(...)` — i.e. if the WhatsApp/Telegram sender fails specifically because Twilio isn't configured and the destination is really a plain phone number, the scheduler automatically falls back to sending it as an SMS instead of failing the event. If the guard doesn't match, the original exception is re-raised and handled by the outer `except Exception` (see retry/backoff below).
   - On success, `_mark_notification_event_status(notification_id=..., status="SENT", provider_message_sid=str(provider_sid or ""), channel_account_id=..., doctor_id=..., admin_id=event.admin_id)` is recorded.

**Top-level failure handling**: any uncaught exception from the above is caught once at the top of `_process_notification_event`, computing an exponential backoff `backoff = min(1800, 60 * (2 ** attempt_count))` and calling `mark_notification_event_retry(notification_id=..., error_text=str(exc), backoff_seconds=backoff, max_attempts=5)`, returning `False` (counted as `event_failed`).

### `_process_sms_notification_event`

- Instantiates `SMSNotificationService(self._settings, LOGGER)` (from `src.runtime.sms_notification_service`, imported lazily inside the method).
- Determines whether SMS is allowed for this event's originating channel: reads `event.meta_json["source_channel"]` (or `event.source_channel` fallback) and checks `sms_service.is_sms_enabled_for_channel(source_channel)` if known, else `sms_service.sms_enabled`. If disallowed, the event is marked `"SKIPPED"` (not retried) and treated as handled (`return True`).
- Guards against stale confirmations: `_is_stale_confirmation_sms` — for `event_type == "CONFIRMATION"` events whose `slot_date`/`slot_time` is already in the past (IST), the event is retried with `backoff_seconds=1, max_attempts=1` (effectively drops it after one more no-op pass) rather than texting a patient about an appointment time that's already elapsed.
- Builds the SMS body via `_build_sms_message(event)`, which delegates to `sms_service.build_message_by_event_type(event_type=..., patient_name=..., doctor_name=..., appointment_date=..., appointment_time=..., clinic_name=..., doctor_slug=...)`. An empty result is treated as a build failure and retried with exponential backoff.
- Sends via `sms_service.send_sms_with_credit_check(doctor_id=..., appointment_id=..., phone_number=to_number, message=message)`, which returns `(success, provider_sid, failure_reason)` — this is the **credit reserve/release** integration point: the SMS service itself reserves a doctor's SMS credit before sending and releases/consumes it based on outcome (implementation lives in `SMSNotificationService`, outside this module).
  - `success` → marked `"SENT"`.
  - `failure_reason == "CREDITS_EXHAUSTED"` → retried with `backoff_seconds=1, max_attempts=1` (drop after one more pass; avoids hot-looping once a doctor is out of SMS credits).
  - `failure_reason in ("SERVICE_DISABLED", "SMS_SERVICE_UNAVAILABLE")` → marked `"FAILED"` outright (not retried — these are configuration-level, not transient).
  - Any other failure → retried with exponential backoff (`min(1800, 60 * 2**attempt_count)`), `max_attempts=5`.
  - Any exception during the send call itself is caught and also retried with the same exponential backoff scheme.

## 3. Report file cleanup

`_cleanup_report_files()` is invoked from inside `_run_reminders_once()`, gated by a monotonic-clock throttle so it only actually runs once per `REPORT_CLEANUP_INTERVAL_SECONDS` (default 3600s / 1 hour), not on every reminder tick:

```python
now_monotonic = time.monotonic()
if now_monotonic >= self._next_report_cleanup_at:
    removed = self._cleanup_report_files()
    ...
    self._next_report_cleanup_at = now_monotonic + self._report_cleanup_interval_seconds
```

Cleanup logic, scoped to `data/reports/` (returns 0 immediately if that directory doesn't exist):

1. Lists every file (non-directories) in the folder with its `mtime`.
2. Any file whose `mtime` is older than `now - REPORT_RETENTION_DAYS * 86400` seconds (default retention 14 days) is marked for removal.
3. Of the files *not* already marked, if the remaining count exceeds `REPORT_MAX_FILES` (default 5000), the oldest-first overflow (sorted by `mtime` ascending) is also marked for removal, down to the cap.
4. All marked files are deleted (`os.remove`, individual `OSError`s swallowed so one bad file doesn't abort the sweep); the count actually removed is returned and logged.

This bounds unbounded growth of the `.xlsx` files generated by the doctor-reminder loop (`_build_doctor_report_xlsx`) by both age and total count.

## 4. Constructor / dependency wiring

```python
AutomationScheduler(
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
)
```

- **`settings`** — passed through opaquely; only used later to construct `SMSNotificationService(self._settings, LOGGER)` inside the SMS path. Not read directly by the scheduler otherwise.
- **`booking_repository`** — a `BookingRepository` (or any object exposing the same method surface: `list_due_doctor_reminders`, `get_extra_doctor_contacts`, `is_reminder_sent`, `insert_or_get_reminder_queue`, `mark_reminder_sent`, `mark_reminder_failed`, `claim_pending_notification_events`, `mark_notification_event_retry`, `mark_notification_event_status`). If `None` (or falsy), both loops effectively no-op — `start()` won't spawn the thread, and `_run_reminders_once`/`_run_event_notifications_once` return immediately. This is the main seam for testing: pass a mock/fake repository to control exactly what "due" rows and "pending events" look like.
- **`send_message_fn`** — `(to_number: str, body: str) -> object` (return value is treated as a provider message SID, best-effort `str()`-cast). This is the single generic text-sending hook used by both the reminder loop's no-document fallback and the event-notification loop's telegram/whatsapp branch. In `main.py` it's a lambda wrapping `_send_plain_channel_message`.
- **`send_document_fn`** — optional `(to_number, file_path, caption) -> None`. If omitted, the reminder loop degrades to sending a text message that only references the generated report filename rather than attaching the file.
- **`source_whatsapp_number`** — the bot's own WhatsApp sending number (normalized via `_normalize_whatsapp_number`), used to filter it out of a doctor's own destination list (so the bot never "reminds" its own outbound number).
- **`enabled`** — master on/off switch (`AUTOMATION_ENABLED`); when `False`, `start()` does nothing at all (not even the Kafka bridge is started).
- **`doctor_reminder_enabled`** — (`DOCTOR_REMINDER_ENABLED`) gates only the reminder thread; note that because the event-notification loop is invoked from inside the reminder loop, disabling reminders also disables event-notification processing — there is no independent way to run just the event loop.
- **`doctor_reminder_interval_seconds` / `doctor_reminder_lead_minutes` / `doctor_reminder_window_seconds` / `doctor_reminder_lead_minutes_list`** — see the reminder-loop section above for exact semantics.
- **`resolve_channel_account_id_fn`** — `(channel: str, doctor_id: int) -> Optional[int]`, used by both loops to look up a multi-account routing id and rewrite the destination via `build_scoped_user_id`. Passing `None` disables account-scoping entirely (destinations are used as-is).
- **`notification_bridge`** — optional `KafkaNotificationBridge`; when present, claimed notification events are routed through it (`process_pending_events`) instead of being processed inline. `main.py` attaches this *after* construction by setting `automation_scheduler._notification_bridge` directly (not via the constructor kwarg), wiring the bridge's `process_event_fn` back to `automation_scheduler._process_notification_event` — so the same per-event logic (including the SMS-recovery fallback) applies whether or not Kafka is in the path. `start()`/`stop()` call `self._notification_bridge.start()`/`.stop()` if set.

Two additional dependencies are read directly from environment variables inside `__init__` rather than being constructor parameters: `REPORT_RETENTION_DAYS`, `REPORT_MAX_FILES`, `REPORT_CLEANUP_INTERVAL_SECONDS` (see the env var table below) — so tests that want to override report-cleanup behavior need to set these env vars before constructing the scheduler, not pass them as kwargs.

For testing/extension: the cleanest integration point is a fake `booking_repository` implementing the methods listed above, plus stub `send_message_fn`/`send_document_fn` callables recording their calls. Because `_PersistentKeyStore` always writes to `data/doctor_reminder_keys.jsonl` relative to the process's working directory, tests that exercise dedup repeatedly should either run in an isolated working directory or delete that file between runs.

## 5. Lifecycle: `start()` / `stop()` / `snapshot()`

- **`start()`** — no-op if `not self._enabled` or if threads are already running (`self._threads` non-empty, i.e. idempotent against double-start). Otherwise starts the notification bridge (if any) and, if reminders are enabled and a repository is present, spawns the `"doctor-reminder"` daemon thread.
- **`stop()`** — sets `self._stop`, joins all tracked threads with a 2-second timeout each, clears the thread list, and stops the notification bridge (if any). Safe to call even if `start()` was never called or was a no-op.
- **`snapshot()`** — returns a plain dict combining the live `_metrics` counters (`reminder_runs`, `reminder_errors`, `reminder_sent`, `reminder_skipped`, `event_runs`, `event_sent`, `event_failed`) with `alive_workers` (count of tracked threads still alive) and `notification_bridge` (the bridge's own `.snapshot()` dict, or `{}` if no bridge is configured). This is what `main.py`'s `GET /health/queue` endpoint exposes verbatim under the `"automation"` key, alongside queue/overflow/notification-queue/dedup diagnostics for the rest of the app.

## 6. Environment variables

| Variable | Default | Read by | Effect |
|---|---|---|---|
| `AUTOMATION_ENABLED` | `true` | `src/config.py` → `enabled` ctor arg | Master switch; `start()` is a full no-op when false. |
| `DOCTOR_REMINDER_ENABLED` | `true` | `src/config.py` → `doctor_reminder_enabled` ctor arg | Gates the `"doctor-reminder"` thread (and, transitively, event-notification processing since it runs inside the same loop). |
| `DOCTOR_REMINDER_INTERVAL_SECONDS` | `60` | `src/config.py` → `doctor_reminder_interval_seconds` ctor arg | Reminder-loop tick interval (clamped to a minimum of 5s inside the scheduler). |
| `DOCTOR_REMINDER_LEAD_MINUTES` | `10` | `src/config.py` → `doctor_reminder_lead_minutes` ctor arg | Fallback single lead time; used to derive the default two-element lead list (`[60, this]`) when `DOCTOR_REMINDER_LEAD_MINUTES_LIST` isn't set. |
| `DOCTOR_REMINDER_LEAD_MINUTES_LIST` | unset (derives `[60, 10]`) | `main.py` (parsed, comma-separated ints) → `doctor_reminder_lead_minutes_list` ctor arg | Explicit list of lead times (minutes-before-slot) at which a report is generated and sent, e.g. `"60,10"`. |
| `DOCTOR_REMINDER_WINDOW_SECONDS` | `30` | `src/config.py` → `doctor_reminder_window_seconds` ctor arg | Half-width of the "in window" band around each lead time's `center_seconds` (clamped to a minimum of 5s). |
| `REPORT_RETENTION_DAYS` | `14` | `scheduler.py` `__init__` directly (`os.getenv`) | Age threshold beyond which `.xlsx` files in `data/reports/` are deleted by `_cleanup_report_files`. |
| `REPORT_MAX_FILES` | `5000` | `scheduler.py` `__init__` directly (`os.getenv`) | Cap on total files retained in `data/reports/`; oldest-first deletion once exceeded. |
| `REPORT_CLEANUP_INTERVAL_SECONDS` | `3600` | `scheduler.py` `__init__` directly (`os.getenv`) | Minimum spacing (via `time.monotonic()`) between actual cleanup sweeps, checked once per reminder tick. |

Note the asymmetry: the `DOCTOR_REMINDER_*` variables are read once in `src/config.py`'s settings loader and passed into the constructor as explicit kwargs (except `DOCTOR_REMINDER_LEAD_MINUTES_LIST`, which `main.py` parses itself), whereas the three `REPORT_*` variables are read directly by `AutomationScheduler.__init__` via `os.getenv`, with no corresponding constructor parameters — they cannot be overridden per-instance except by setting the environment variable before construction.
