# Data access layer (`src/repositories`)

This package is the only place in the codebase that speaks SQL. Every FSM
handler, HTTP route, background worker, and scheduler goes through one of
the repository classes here rather than opening its own MySQL connection.
The package is split by table/subsystem ownership (booking, conversations,
scheduling/availability, multi-account routing, notifications, doctor
reminders, Evolution API bindings), and the two biggest repositories —
`BookingRepository` and, to a lesser extent, `NotificationRepository` /
`ReminderRepository` — are further split across multiple files so no single
file becomes unmanageable. Connections come from a small pooling helper in
`src/db/connection.py` that every repository shares.

Back to [root README](../../README.md).

## Contents

- [Architecture pattern: facade + ops-module delegation](#architecture-pattern-facade--ops-module-delegation)
- [`src/db/connection.py`](#srcdbconnectionpy)
- [`BookingRepository` (`booking_repository.py`, `booking_query_ops.py`, `booking_write_ops.py`)](#bookingrepository-booking_repositorypy-booking_query_opspy-booking_write_opspy)
- [`ConversationRepository`](#conversationrepository)
- [`SchedulingRepository`](#schedulingrepository)
- [`ChannelAccountRepository`](#channelaccountrepository)
- [`NotificationRepository` / `notification_ops.py`](#notificationrepository--notification_opspy)
- [`ReminderRepository` / `reminder_ops.py`](#reminderrepository--reminder_opspy)
- [`EvolutionRepository`](#evolutionrepository)
- [File summary](#file-summary)

## Architecture pattern: facade + ops-module delegation

This is **not** one class per file. Concretely, for booking:

- `booking_repository.py` defines the single class other code imports and
  calls: `BookingRepository`. It owns `__init__`, the MySQL connection
  helper (`_connect`), all the schema-introspection caching
  (`_table_exists`, `_table_columns`, `_appointment_table`,
  `_use_appointment_mode`, etc.), the Redis-backed patient-identity cache,
  and the slot/queue-number math.
- `booking_query_ops.py` and `booking_write_ops.py` are **plain modules of
  free functions**, not classes and not mixins. Each function's first
  positional parameter is named `repo` and is expected to be a
  `BookingRepository` instance — the function calls back into `repo._connect()`,
  `repo._appointment_table()`, `repo._use_appointment_mode()`,
  `repo._normalize_phone()`, etc. to reuse the facade's cached
  metadata/connection logic.
- `BookingRepository` imports those functions under aliased names (e.g.
  `from src.repositories.booking_query_ops import find_active_appointment_by_phone_number as _find_active_appointment_by_phone_number`)
  and exposes a thin **wrapper method of the same public name** that just
  forwards `self` and the call's arguments to the free function, e.g.:

  ```python
  def find_active_appointment_by_phone_number(self, phone_number, admin_id=None, doctor_id=None):
      return _find_active_appointment_by_phone_number(
          self, phone_number=phone_number, admin_id=admin_id, doctor_id=doctor_id,
      )
  ```

  This is manual delegation (explicit `self`-forwarding), not
  `types.MethodType` assignment and not multiple inheritance. Callers never
  know the difference — `BookingRepository()` instances expose every method
  from both ops modules as if they were defined directly on the class.
- The same pattern is reused for two more concerns that live logically
  under "booking" but are split into their own ops modules and imported
  into `BookingRepository` the same way: `notification_ops.py` (functions
  prefixed nowhere, imported as `_log_notification_event`,
  `_list_pending_notification_events`, etc.) and `reminder_ops.py`
  (`_get_appointment_status`, `_list_due_doctor_reminders`,
  `_get_extra_doctor_contacts`, `_is_reminder_sent`,
  `_insert_or_get_reminder_queue`, `_mark_reminder_sent`,
  `_mark_reminder_failed`). So `BookingRepository` ends up exposing
  notification-log and doctor-reminder-queue methods too, all backed by the
  same MySQL connection/pool and the same appointment-table detection logic
  as booking itself.

Important nuance: **`NotificationRepository` and `ReminderRepository` are
separate, fully self-contained classes** (see below) that duplicate the same
SQL as `notification_ops.py`/`reminder_ops.py` rather than being built from
those modules. In practice the app only instantiates `NotificationRepository`
transiently, inside `BookingRepository.ensure_notification_schema()`, to run
its `ensure_notification_schema()` DDL — all *runtime* notification/reminder
reads and writes actually go through `BookingRepository`'s wrapper methods
(which call the `*_ops.py` free functions against the booking connection/
cache). `ReminderRepository` as a class does not appear to be instantiated
anywhere in the running app at all — only its `DoctorReminder` dataclass is
imported (by `booking_repository.py`) and reused as the return type for
`BookingRepository.list_due_doctor_reminders()`. Treat `NotificationRepository`
and `ReminderRepository` as the schema/reference implementation for their
tables, but expect to edit `notification_ops.py` / `reminder_ops.py` (via
`BookingRepository`) when changing actual runtime behavior.

## `src/db/connection.py`

Small, dependency-light MySQL connection-pooling helper used by every
repository in this package.

- **`MySQLConfig`** — frozen dataclass: `user`, `password`, `host`, `port`,
  `database`.
- **`parse_mysql_url(database_url: str) -> MySQLConfig`** — parses a
  `DATABASE_URL` of the form `mysql+mysqlconnector://user:password@host:port/dbname`.
  It rewrites the `mysql+mysqlconnector://` prefix to `mysql://` and then
  uses `urllib.parse.urlparse`, URL-decoding the password. Every repository
  is ultimately constructed with a `MySQLConfig` produced this way (see
  `src/db_store.py::_config_from_env`, which requires the URL to literally
  start with `mysql+mysqlconnector://` or it treats the DB as unconfigured).
- **Connection pooling** — a process-wide `dict` of
  `mysql.connector.pooling.MySQLConnectionPool` instances, keyed by
  `(host, port, database, user)`, guarded by a `threading.Lock`
  (`_pools`, `_pools_lock`, `_pool_key`). One pool is lazily created per
  distinct DB target with `pool_size=10` and `pool_reset_session=True`.
- **`connect_mysql(config: MySQLConfig)`** — the only entry point
  repositories use. It fetches a connection from the pool, pings it
  (`reconnect=True`) to verify it's alive, and retries once (dropping and
  recreating the pool via `_drop_pool` on `mysql.connector.Error`) before
  giving up and raising. Every repository method calls
  `self._connect()` → `connect_mysql(self._config)` at the top and
  `conn.close()` in a `finally` block, which returns the connection to the
  pool rather than actually closing the TCP connection.
- No repository holds a long-lived connection; each method call is
  connect → query → commit/rollback → close (return to pool).

## `BookingRepository` (`booking_repository.py`, `booking_query_ops.py`, `booking_write_ops.py`)

The central, highest-traffic repository. Owns/touches: `patients`,
`appointment`/`appointments` (whichever exists — see "dual schema mode"
below), `slots` (legacy schema only), `doctor_clinic_schedule`, `clinics`,
`doctors`, `admins`, plus (via the ops-module delegation above)
`appointment_notification_log`, `message_delivery_status`, and
`doctor_remainder_queue`.

**Dual schema mode.** The repo detects at runtime, via
`INFORMATION_SCHEMA`, whether the DB uses the newer flattened
`appointment` table (with `appointment_date`/`start_time`/`end_time`
columns directly on the row) or the older `appointments` + `slots` join
model. `_appointment_table()` picks `"appointment"` if it exists, else
`"appointments"`. `_use_appointment_mode()` is `True` only when
`appointment` exists **and** `slots` does not. Almost every query method
branches on `_use_appointment_mode()` to run one of two SQL shapes. Table
existence, column existence, and the appointment-table/mode decisions are
all cached in-process (`_table_exists_cache`, `_table_columns_cache`,
`_appointment_table_cache`, `_use_appointment_mode_cache`, guarded by
`_meta_cache_lock`); `_invalidate_table_columns_cache(...)` is called after
any DDL that adds columns.

**Redis-backed patient identity cache.** `set_redis_client()` wires an
optional Redis client. `find_patient_name_by_phone_number` /
`find_patient_name_by_chat_user_id` / `find_patient_phone_by_chat_user_id`
check a small JSON cache (`_load_cached_patient_identity`,
`_save_cached_patient_identity`, keyed by
`{prefix}:patient:phone:{admin}:{doctor}:{phone}` or
`{prefix}:patient:chat:...`, 30 min TTL) before hitting MySQL, and populate
it after a DB hit or a successful `save_confirmed_appointment`.

### Public method groups

| Group | Methods |
|---|---|
| Schema / startup | `ensure_appointment_columns()` — ALTERs `appointment`/`appointments` to add `cancelled_by`, `rescheduled_by`, `notify_telegram_chat_id`, `booking_id`, `booked_for`, `channel` if missing. `ensure_notification_schema()` — delegates to a transient `NotificationRepository(self._config).ensure_notification_schema()`. |
| Patient / doctor lookup (→ `booking_query_ops.py`) | `get_doctor_display_name`, `find_patient_name_by_phone_number`, `find_patient_name_by_chat_user_id`, `find_patient_phone_by_chat_user_id` |
| Active-appointment lookups (→ `booking_query_ops.py`) | `find_active_appointment_by_patient_name`, `find_active_appointment_by_phone_number`, `list_active_appointments_by_phone_number`, `list_active_appointments_by_chat_user_id` — all filter to `status IN ('BOOKED','PENDING','CONFIRMED')` and drop rows that are in the past via `_is_actionable_booking_row` |
| Booking CRUD (→ `booking_write_ops.py`) | `save_confirmed_appointment(context, admin_id, doctor_id) -> BookingResult` (patient upsert + slot/time validation + insert-or-reuse-cancelled-row, dedupes by patient+doctor+date+time, enforces "max 1 active OTHER-booked-for appointment"), `cancel_appointment(appointment_id, admin_id, cancelled_by) -> bool`, `reschedule_appointment_same_clinic(appointment_id, new_date, new_time, new_clinic_id, admin_id, rescheduled_by) -> BookingResult` |
| Queue number | `get_daily_queue_number(appointment_id)` — computes the 1-based position of a booked slot within its schedule window (`_compute_slot_position` / `_session_slot_index`), independent of other bookings |
| Notification pass-throughs (→ `notification_ops.py`) | `log_notification_event`, `log_doctor_delayed_notification`, `list_pending_notification_events`, `mark_notification_event_status`, `claim_pending_notification_events`, `mark_notification_event_retry`, `upsert_delivery_status`, `notification_queue_stats` |
| Reminder pass-throughs (→ `reminder_ops.py`) | `get_appointment_status`, `list_due_doctor_reminders`, `get_extra_doctor_contacts`, `is_reminder_sent`, `insert_or_get_reminder_queue`, `mark_reminder_sent`, `mark_reminder_failed` |
| Misc | `default_admin_id()`, `set_redis_client()` |

`save_confirmed_appointment` also does side-effect work after a successful
insert: it refreshes the Redis patient-identity cache and — if
`context.booking_channel` and a phone number are present — logs a
`CONFIRMATION` / `sms` notification event via `log_notification_event` so
the SMS worker can decide whether to actually send (gated by
`SMS_ENABLED_CHANNELS`, not decided here).

`BookingResult` (dataclass): `ok: bool`, `message: str`,
`appointment_id: Optional[int]`, `queue_number: Optional[int]`.

**Startup wiring** (`main.py`): `booking_repository.ensure_appointment_columns()`
then `booking_repository.ensure_notification_schema()`, both inside
`startup_validation()`, best-effort (exceptions logged, not raised).

## `ConversationRepository`

Owns `conversation_sessions`, `inbound_message_sids`, `inbound_turn_queue`.

- **`ensure_schema()`** — creates all three tables if missing, adds the
  `idx_inbound_message_received` index and the `fsm_extra_json` column as
  live migrations if the table pre-dates them. Idempotent per-instance via
  `self._schema_ready` flag (only runs the DDL once per process); every
  other public method calls `ensure_schema()` first as a safety net.
- **Session snapshots** — `load_session(user_id, ttl_minutes) -> Optional[SessionSnapshot]`
  (only returns rows updated within the TTL window) and
  `save_session(*, user_id, state, context: dict, response_language,
  language_locked, language_turn_count, init_unclear_count, in_edit_flow,
  doctor_id, admin_id, fsm_extra_json=None)` (upsert via
  `ON DUPLICATE KEY UPDATE`). `SessionSnapshot` dataclass fields:
  `user_id, state, context_json, response_language, language_locked,
  language_turn_count, init_unclear_count, in_edit_flow, doctor_id,
  admin_id, updated_at, fsm_extra_json`.
- **Message-SID dedup** — `seen_or_add_message_sid(message_sid, user_id, body) -> bool`
  (returns `True` if the SID was already seen, via catching
  `mysql.connector.errors.IntegrityError` on the `PRIMARY KEY` insert),
  `dedup_size()`, `purge_old_message_sids(retention_days=30, batch_size=5000)`
  (deletes in batches until a batch comes back short).
- **Inbound-turn overflow queue** (`inbound_turn_queue`, used when the
  in-process turn queue is full) — `enqueue_overflow_turn(*, inbound_sid,
  from_number, body, pre_state)` (upsert, revives non-DEAD rows to
  PENDING), `claim_overflow_turns(*, limit, worker_id) -> list[QueuedTurn]`
  (`FOR UPDATE SKIP LOCKED` with a plain `FOR UPDATE` fallback for older
  MySQL, only claims rows whose lock is stale or absent), `mark_overflow_turn_done(*, queue_id)`,
  `mark_overflow_turn_retry(*, queue_id, error_text, backoff_seconds,
  max_attempts)` (marks `DEAD` once `attempt_count >= max_attempts`),
  `release_overflow_turn(*, queue_id, reason="", backoff_seconds=1)`,
  `overflow_queue_stats() -> dict` (`queued`/`processing`/`dead` counts).
  `QueuedTurn` dataclass: `queue_id, inbound_sid, from_number, body,
  pre_state, attempt_count`.

**Startup wiring**: `conversation_repository.ensure_schema()` is called
directly in `main.py::startup_validation()`.

## `SchedulingRepository`

Owns/touches `clinics`, `doctor_clinic_schedule`, `doctors`, `doctor_leaves`,
`appointment` (read-only, for booked-slot exclusion), plus the
`doctor_cache_invalidation_queue` table (and a set of MySQL triggers on
`doctors`, `doctor_clinic_schedule`, `clinics`, `patients`, `appointment`) and
`schedule_rebuild_queue`.

- **Clinic/date/time discovery** — `list_clinics_for_doctor`,
  `list_available_dates`, `list_available_times` all read from an
  **availability snapshot** rather than querying live per call.
  `ClinicOption` dataclass: `clinic_id, clinic_name, location, today_slots`.
- **Redis-backed availability snapshot cache.** `get_availability_snapshot(doctor_id, admin_id=None)`
  is the public entry point; internally `_get_availability_snapshot` tries
  Redis first (`{prefix}:avail:{admin}:{doctor}:{YYYYMMDD}`, TTL from
  `set_cache_config`/constructor, default 3600s) and, on a miss, calls
  `_build_availability_snapshot` (walks `doctor_accept_days()` days ahead,
  per clinic, computing open slot-start times from
  `doctor_clinic_schedule` windows minus already-booked times minus
  `doctor_leaves` blocks minus already-elapsed same-day times) and caches
  the result. The snapshot payload shape is
  `{doctor_id, admin_id, accept_days, generated_on, clinics,
  dates_by_clinic, times_by_clinic_date, time_end_by_clinic_date}`.
  `invalidate_cached_availability(doctor_id, admin_id)` deletes the cached
  key(s) (scans for all admins if `admin_id` is `None`).
- **Doctor resolution helpers** — `default_doctor_id`,
  `default_doctor_id_by_phone`, `default_doctor_id_by_username` (caches
  which column on `doctors` holds the username), `default_doctor_id_by_chat_id`,
  `doctor_accept_days` (reads from cached snapshot first, falls back to the
  `doctors.acceptdays`/`accept_days` column — column name auto-detected and
  cached).
- **Cache-invalidation event queue** — `ensure_cache_invalidation_schema()`
  creates `doctor_cache_invalidation_queue` and a battery of `AFTER
  INSERT/UPDATE/DELETE` triggers on `doctors`, `doctor_clinic_schedule`,
  `clinics`, and conditionally on `patients`/`appointment` (only if those
  tables exist) that write invalidation rows whenever underlying data
  changes — this is how availability/patient caches stay correct across
  multiple app instances without them talking to each other directly.
  `claim_cache_invalidation_events(*, limit, worker_id) -> list[CacheInvalidationEvent]`
  (`FOR UPDATE SKIP LOCKED` pattern, same as the turn queue),
  `mark_cache_invalidation_done(queue_id)`, `release_cache_invalidation(queue_id)`,
  `process_cache_invalidation_event(event)` (dispatches by
  `entity_type` — `APPOINTMENT` patches the cached snapshot's time list in
  place via `_update_cached_slot_time` instead of a full rebuild; `PATIENT`
  clears the Redis patient-identity cache keys; everything else just
  invalidates the doctor's whole availability snapshot).
  `CacheInvalidationEvent` dataclass carries old/new doctor/clinic/admin/
  slot_date/slot_time/status pairs for diffing.
- **Schedule rebuild queue** — `ensure_rebuild_queue_schema()`,
  `list_active_schedule_ids`, `list_pending_schedule_rebuilds`,
  `clear_schedule_rebuild_request`. Note: `generate_slots_for_schedule`,
  `ensure_slot_dedup_index`, `cleanup_future_available_slots`,
  `deduplicate_future_available_slots`, and
  `deduplicate_all_future_available_slots` are present as **no-op stubs**
  (`return` / `return 0` / `return False`) — they exist for API
  compatibility with the older `slots`-table-generation model but do
  nothing under the current (triggerless-slot) design.

**Startup wiring**: `scheduling_repository.ensure_cache_invalidation_schema()`
is called in `main.py::startup_validation()`; a background thread
(`_cache_inv_thread`) then polls `claim_cache_invalidation_events` /
`process_cache_invalidation_event` / `mark_cache_invalidation_done`.

## `ChannelAccountRepository`

Owns/touches `channel_accounts`, `doctor_channel_bindings`,
`route_cache_versions`. This is the resolver behind the multi-tenant
routing described conceptually in the root README's
["Multi-tenant / multi-account routing"](../../README.md#multi-tenant--multi-account-routing)
section — this section only documents the method contracts.

`ChannelAccount` dataclass: `channel_account_id, admin_id, channel,
provider, sender_identity, webhook_path_key, webhook_secret_enc,
credential_json_enc, status, is_primary`, plus a `.credentials() -> dict`
helper that JSON-parses `credential_json_enc` (empty dict on any
parse failure).

| Method | Contract |
|---|---|
| `get_account_by_id(channel_account_id)` | Returns the `ACTIVE` account row by PK, or `None`. |
| `resolve_by_webhook_key(*, channel, webhook_key, webhook_secret="")` | Looks up by `(channel, webhook_path_key)`; if the row has a non-empty `webhook_secret_enc`, the supplied `webhook_secret` must match exactly or the lookup returns `None`. Used for Telegram's per-account keyed webhook. |
| `resolve_by_sender_identity(*, channel, sender_identity)` | Loads all `ACTIVE` rows for the channel, normalizes both DB and input identity (`_normalize_sender_identity`: strips `whatsapp:`, digits-only, adds `+91` for bare 10-digit numbers) and does an in-Python equality scan — no SQL-side normalization. |
| `resolve_binding(channel_account_id)` | Returns `{doctor_id, admin_id, clinic_id, channel_account_id}` for the highest-priority (`is_primary DESC, binding_id ASC`) active `doctor_channel_bindings` row bound to that account, with a second query to pull `admin_id` off `doctors`. `None` if no binding or the binding's doctor has no id. |
| `resolve_account_for_doctor(*, channel, doctor_id)` | Reverse lookup: given a doctor + channel, finds the account via `doctor_channel_bindings JOIN channel_accounts`, both sides `ACTIVE`, ordered by primary flags. |
| `current_route_cache_version()` | Reads the single `route_cache_versions` row for `entity='channel_routing'` (defaults to `1` if missing or on any exception — fails open). |
| `bump_route_cache_version()` | `INSERT ... ON DUPLICATE KEY UPDATE version = version + 1` against the same row (seeds it at `2` if absent), returns the new version. Called by DB triggers indirectly (via app code) and by the `POST /internal/route-cache/invalidate` admin endpoint. |

No `ensure_*_schema()` method here — the `channel_accounts` /
`doctor_channel_bindings` / `route_cache_versions` tables are provisioned by
`migrate_multi_channel_schema.py` / `db_multi_channel_migration.sql`, not by
this repository at app startup.

## `NotificationRepository` / `notification_ops.py`

Owns `appointment_notification_log` and `message_delivery_status`.

`NotificationEvent` dataclass (defined in `notification_repository.py`,
reused by `BookingRepository`'s wrapper methods and constructed by
`notification_ops.py`): `notification_id, appointment_id, event_type,
channel, destination, status, patient_name, clinic_name, slot_date,
slot_time, patient_phone, patient_telegram_chat_id, meta_json, admin_id,
doctor_id=None, channel_account_id=None, attempt_count=0,
source_channel="", doctor_name="", doctor_slug=""`.

- **`ensure_notification_schema()`** (class method on `NotificationRepository`
  only) — creates `appointment_notification_log` (with `attempt_count`,
  `next_retry_at`, `locked_at`, `lock_owner`, `dead_at`, `dead_reason`,
  `provider_message_sid`, `delivery_status`, `delivery_updated_at`,
  `source_channel` added as live-migration ALTERs if missing) and
  `message_delivery_status` (unique on `(provider, provider_message_sid)`).
- **Log / list / claim** — `log_notification_event(*, appointment_id,
  event_type, channel, destination="", status="PENDING", error_text="",
  admin_id=None, meta_json="")`, `log_doctor_delayed_notification(...)`
  (convenience wrapper that sets `event_type="DOCTOR_DELAYED"`),
  `list_pending_notification_events(*, limit=200, admin_id=None)` (only
  `PENDING`, not dead, `next_retry_at` due, `event_type IN
  ('CONFIRMATION','CANCELLED','RESCHEDULED','DOCTOR_DELAYED')`),
  `claim_pending_notification_events(*, limit=100, worker_id, admin_id=None)`
  (transactional claim: `SELECT ... FOR UPDATE SKIP LOCKED` — or plain
  `FOR UPDATE` fallback — over `PENDING`/`FAILED` rows whose lock is stale,
  flips them to `PROCESSING` and increments `attempt_count`).
- **Status / retry** — `mark_notification_event_status(*, notification_id,
  status, error_text="", provider_message_sid="")` (sets `sent_at` only
  when status is `SENT`, clears the lock), `mark_notification_event_retry(*,
  notification_id, error_text, backoff_seconds, max_attempts)` (flips to
  `DEAD` once `attempt_count >= max_attempts`, otherwise schedules
  `next_retry_at`).
- **Delivery-status persistence** — `upsert_delivery_status(*, provider,
  provider_message_sid, channel, message_status, to_number="", from_number="",
  error_code="", error_message="", payload_json="")` — the sink for Twilio
  `/twilio/status`, Meta, and Infobip delivery-status webhook callbacks;
  upserts into `message_delivery_status` on `(provider, provider_message_sid)`
  and mirrors `delivery_status`/`delivery_updated_at` back onto the
  matching `appointment_notification_log` row.
- **`notification_queue_stats() -> dict`** — `{"queued": ..., "dead": ...}`
  counts across `appointment_notification_log`.

`notification_ops.py` re-implements every one of the above (minus
`ensure_notification_schema`) as free functions taking `repo` — with two
material differences from the `NotificationRepository` class versions:
they use `repo`'s dynamic column-existence checks (`_column_exists`) to
conditionally include `doctor_id`/`channel_account_id` columns (for
deployments where those columns exist on `appointment_notification_log`/
`message_delivery_status`), and `list_pending_notification_events`/
`claim_pending_notification_events` additionally join `doctors` to surface
`doctor_name`/`doctor_slug`/`source_channel`/`channel_account_id` on each
`NotificationEvent`. These are the versions actually exercised at runtime,
via `BookingRepository`'s wrapper methods.

**Startup wiring**: only `ensure_notification_schema()` is called at
startup, indirectly, via `booking_repository.ensure_notification_schema()`
in `main.py`.

## `ReminderRepository` / `reminder_ops.py`

Owns `doctor_remainder_queue` (sic — table name has the historical typo)
and reads from `doctors`, `doctor_whatsapp_numbers`, `appointment`/`slots`,
`doctor_clinic_schedule`, `clinics`, `patients`.

`DoctorReminder` dataclass (defined in `reminder_repository.py`, reused by
`BookingRepository.list_due_doctor_reminders()`'s return type):
`appointment_id, doctor_id, doctor_whatsapp, doctor_telegram_chat_id,
patient_name, patient_contact, clinic_name, slot_date, slot_time, status,
booking_number, schedule_id, schedule_start_time, schedule_end_time`.

- **`ensure_reminder_schema()`** — adds a `lead_minutes INT NOT NULL
  DEFAULT 10` column to `doctor_remainder_queue` if the table already
  exists and lacks it. Not called anywhere in `main.py` at the time of
  writing — the table is expected to already exist (created by an earlier
  migration) rather than being auto-created here.
- **Due-reminders lookahead** — `list_due_doctor_reminders(lookahead_minutes=180,
  admin_id=None)`: finds active-status appointments whose start timestamp
  falls within `[now, now + lookahead_minutes]` (IST via
  `CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+05:30')`), joined to the
  doctor's WhatsApp number / Telegram chat id column (both auto-detected
  from `doctors`' actual columns) and the matching
  `doctor_clinic_schedule` row (for the session's start/end time, used to
  decide whether the whole-session-vs-single-appointment reminder should
  fire). Rows with neither a WhatsApp number nor Telegram id, or an
  unresolvable `doctor_id`/`schedule_id`, are dropped.
- **`get_extra_doctor_contacts(doctor_ids)`** — additional WhatsApp
  numbers / Telegram chat IDs per doctor from `doctor_whatsapp_numbers`
  (table may not exist in all deployments — returns `{}` gracefully).
  Returns `{doctor_id: [{"whatsapp": ..., "telegram": ...}, ...]}`,
  deduped.
- **Dedup / queue lifecycle** — `is_reminder_sent(*, dedup_key)`,
  `insert_or_get_reminder_queue(*, doctor_id, schedule_id, slot_date,
  schedule_start_time, schedule_end_time, channel, destination,
  lead_minutes, dedup_key) -> queue_id` (insert-or-return-existing on
  `dedup_key`), `mark_reminder_sent(*, queue_id)`, `mark_reminder_failed(*,
  queue_id, error)` (increments `attempt_count`, truncates error to 250
  chars).
- Also exposes `get_appointment_status(appointment_id)` via
  `reminder_ops.py` only (not present as a `ReminderRepository` class
  method) — a simple status/patient/clinic/slot lookup by appointment id,
  exposed on `BookingRepository` as `get_appointment_status`.

Same duplication note as notifications: `reminder_ops.py` re-implements
everything above as free functions taking `repo`, and — because
`ReminderRepository` the class does not appear to be constructed anywhere
in the running app — these free functions (called through
`BookingRepository`'s wrapper methods) are the actual runtime
implementation; `reminder_repository.py`'s class body is effectively a
reference/duplicate implementation plus the `DoctorReminder` dataclass
definition.

## `EvolutionRepository`

Owns `doctor_evolution_bindings`; reads `doctors`, `clinics`. Used by the
Evolution API bridge (self-hosted/unofficial WhatsApp gateway integration)
described in depth in [`src/evolution/README.md`](../evolution/README.md) —
this section only covers the method contract.

- **`ensure_schema()`** — creates `doctor_evolution_bindings` (unique on
  both `evolution_instance_name` and `evolution_account_identity`). Called
  directly on the module-level `evolution_repository` in `main.py` at
  startup (best-effort, wrapped in try/except).
- **`resolve_doctor_context(*, instance_name="", connected_account="")
  -> Optional[EvolutionDoctorContext]`** — tries `instance_name` first
  (exact match against `doctor_evolution_bindings.evolution_instance_name`,
  status `ACTIVE`), then falls back to `connected_account` (normalizes both
  sides to digits-only via `_normalize_identity` and linear-scans active
  `doctors.phone` values for a match). Either path fills in a fallback
  clinic (`_first_clinic_for_doctor`) if the binding doesn't specify one or
  the resolved clinic has no name.
- `EvolutionDoctorContext` dataclass (frozen): `doctor_id, admin_id,
  clinic_id, instance_name, account_identity, doctor_name, clinic_name,
  slug`.

## File summary

| File | Approx LOC | One-line responsibility |
|---|---:|---|
| `booking_repository.py` | 917 | `BookingRepository` facade: connection/schema-cache plumbing, patient identity Redis cache, queue-number math, wrapper methods delegating to the three ops modules below |
| `booking_query_ops.py` | 673 | Free functions (take `repo`) for patient/doctor lookups and active-appointment reads, used by `BookingRepository` |
| `booking_write_ops.py` | 1515 | Free functions (take `repo`) for `cancel_appointment`, `reschedule_appointment_same_clinic`, `save_confirmed_appointment` — the core booking transaction logic |
| `conversation_repository.py` | 544 | `ConversationRepository`: session snapshots, message-SID dedup, inbound-turn overflow queue |
| `scheduling_repository.py` | 1403 | `SchedulingRepository`: clinic/date/time availability (Redis-cached snapshot), cache-invalidation trigger/queue plumbing, doctor-lookup helpers |
| `channel_account_repository.py` | 346 | `ChannelAccountRepository`: multi-account routing resolution (`channel_accounts`, `doctor_channel_bindings`, `route_cache_versions`) |
| `notification_repository.py` | 785 | `NotificationRepository` class: full standalone implementation of notification-log/delivery-status CRUD plus `ensure_notification_schema()` (the schema-DDL entry point actually used at startup) |
| `notification_ops.py` | 664 | Free functions (take `repo`) duplicating `NotificationRepository`'s CRUD with dynamic column detection; wired into `BookingRepository` and used at runtime |
| `reminder_repository.py` | 512 | `ReminderRepository` class: standalone implementation of doctor-reminder-queue CRUD + `DoctorReminder` dataclass (class itself not instantiated by the running app) |
| `reminder_ops.py` | 425 | Free functions (take `repo`) duplicating `ReminderRepository`'s CRUD; wired into `BookingRepository` and used at runtime |
| `evolution_repository.py` | 183 | `EvolutionRepository`: resolves an Evolution API instance/account to a doctor/clinic context |
| `__init__.py` | 11 | Re-exports `BookingRepository`, `BookingResult`, `ClinicOption`, `ConversationRepository`, `SchedulingRepository` |
