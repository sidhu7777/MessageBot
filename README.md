# MessageBot — Multi-Channel Clinic Appointment Platform

FastAPI service that lets patients book, check, reschedule, and cancel doctor
appointments over **WhatsApp (Twilio / Meta Cloud API / Infobip)**,
**Telegram**, a **QR-code check-in flow**, and a **browser booking widget** —
all backed by one FSM-driven conversation engine, an Ollama LLM for
intent/extraction, MySQL for persistence, and Redis for caching/locking.

It is **multi-tenant**: many admins can each run many doctors, and each
doctor can be bound to its own WhatsApp/Telegram bot credentials
(`channel_accounts` + `doctor_channel_bindings`), so multiple independent
clinics can run off the same deployment with fully isolated conversations.

This file is the project hub: a full overview of how the system works, plus
links to per-module READMEs that go deeper on each subsystem.

## Contents

- [What this project does](#what-this-project-does)
- [Channels](#channels)
- [Multi-tenant / multi-account routing](#multi-tenant--multi-account-routing)
- [Request lifecycle (architecture)](#request-lifecycle-architecture)
- [Module map & links](#module-map--links)
- [Project structure](#project-structure)
- [Database](#database)
- [Redis](#redis)
- [Deployment (Docker)](#deployment-docker)
- [Environment variables](#environment-variables)
- [Run & test](#run--test)
- [Data/artifacts written at runtime](#dataartifacts-written-at-runtime)
- [Known dead / experimental code](#known-dead--experimental-code)

## What this project does

A patient messages a clinic's WhatsApp number, Telegram bot, scans a QR code
at reception, or opens a web link, and can:

- Book a new appointment (choose clinic, date, time, who it's for).
- Check a doctor's availability without booking.
- See, reschedule, or cancel an existing active appointment (capped at 2
  active bookings per phone number).
- Get auto-filled details if they're a known/returning patient.
- Converse in English, Hindi, or Hinglish — language is auto-detected and
  then locked for the rest of the conversation.

On the clinic side, the platform also:

- Sends each doctor a reminder message + an Excel patient list before their
  session (configurable lead times, e.g. 60 and 10 minutes before).
- Sends confirmation/cancellation/reschedule notifications to patients over
  WhatsApp, Telegram, or SMS.
- Lets reception generate/print QR codes for walk-in check-in, per doctor or
  hospital-wide.

## Channels

| Channel | Direction | Goes through the FSM? | Notes |
|---|---|---|---|
| **Twilio WhatsApp** | inbound + outbound | Yes | Production. Signature validation optional (`ENABLE_TWILIO_SIGNATURE_VALIDATION`). Delivery-status callbacks at `/twilio/status`. |
| **Meta WhatsApp Cloud API** | inbound + outbound | Yes | Production. Webhook verify handshake + HMAC-SHA256 signature validation. |
| **Infobip WhatsApp** | inbound + outbound | Yes | Production. Own webhook + delivery-status path. |
| **Telegram** | inbound + outbound | Yes | Production. Supports a legacy single-bot webhook and a per-account keyed webhook (`/telegram/webhook/{webhook_key}`) for multi-bot routing. |
| **Evolution API** (self-hosted/unofficial WhatsApp gateway) | inbound only | **No** | Doesn't drive the FSM — on a patient's first message in a session window it auto-replies once with a link to the web booking widget, warns once on a second message, then goes silent. See [src/evolution/README.md](src/evolution/README.md). |
| **Web booking widget** (`/whatsapp/web/*`) | n/a (HTTP) | **No** | Plain HTML/JS page that talks directly to the booking repository (book/cancel/reschedule/lookup). Despite the route name, no WhatsApp is involved. See [src/whatsapp_web/README.md](src/whatsapp_web/README.md). |
| **QR check-in** (`/qr/*`) | n/a (HTTP) | **No** | Patient scans a printed QR, fills a short web form, gets booked/queued directly. See [src/qr/README.md](src/qr/README.md). |
| **SMS** | outbound only | No (notification pipeline only) | Confirmation/cancellation/reschedule texts via a custom SMS gateway with a credit reserve/release protocol. Gated by `SMS_ENABLED` + `SMS_ENABLED_CHANNELS`. |

WhatsApp specifically has **three interchangeable provider backends**
(Twilio / Meta / Infobip). `WHATSAPP_PROVIDER=auto|twilio|meta|infobip`
picks globally, or a `channel_accounts` row can pin a specific doctor's bot
to a specific provider with its own credentials.

## Multi-tenant / multi-account routing

The system is not "one bot" — it's admins → doctors → bots:

- **`channel_accounts`** — one row per bot credential set (channel:
  telegram/whatsapp, provider: telegram/twilio/meta/infobip, sender
  identity, Telegram webhook key, encrypted credentials).
- **`doctor_channel_bindings`** — maps a doctor (+ optional clinic) to a
  `channel_account`.
- **`route_cache_versions`** — a version counter, bumped by DB triggers
  whenever accounts/bindings change, used to invalidate the in-process +
  Redis routing cache without a restart.
- **Scoped user IDs** — every inbound sender is turned into
  `acct:{channel_account_id}|{raw_user_id}` (`src/runtime/account_scope.py`)
  before touching sessions, locks, dedup, or delivery. This means the same
  phone number talking to two different clinics' bots gets two fully
  isolated conversations.
- **`CHANNEL_ROUTING_STRICT=true` (default)** — if an inbound sender can't
  be resolved to a registered `channel_accounts` row, the message is
  **dropped** rather than silently handled by a default bot.
- **Admin cache-bust endpoint** — `POST /internal/route-cache/invalidate`
  (token-protected) lets an admin panel force-refresh routing immediately
  after provisioning a new bot.

Full route-level detail: [src/api/README.md](src/api/README.md).

## Request lifecycle (architecture)

```
inbound webhook (Twilio/Meta/Infobip/Telegram)
  → ack fast (HTTP 200 immediately)
  → dedup by provider message SID (PersistentMessageSidStore, JSONL-backed)
  → resolve channel_account / doctor / admin (routing cache, L1 + Redis)
  → per-user processing guard (Redis SET NX, one in-flight turn per user)
      ├─ if busy: buffer/collapse into UserTurnBuffer (dedupe near-duplicate
      │  low-intent messages within a coalescing window)
      └─ if free: submit to the turn queue
  → TurnQueueProcessor (bounded worker pool, retry + timeout watchdog)
      optionally fronted by KafkaTurnBridge for multi-instance scale-out
  → SessionManager loads the AppointmentFSM (Redis snapshot, else MySQL)
  → FSM.handle(message)
      NLU: cheap regex/keyword rules first → Ollama LLM fallback for
      ambiguous intent, language, extraction, or abuse detection
      (all Ollama calls are serialized process-wide via a semaphore)
  → reply sent back via ChannelDelivery (provider-specific send call)
  → FSM/session saved back to Redis + MySQL
  → on booking/cancel/reschedule: a NotificationEvent is queued and
    delivered (WhatsApp/Telegram/SMS) by AutomationScheduler, optionally via
    KafkaNotificationBridge
```

If the in-memory queue is full, turns fall back to a MySQL-backed overflow
queue (`inbound_turn_queue`) and are re-polled by a background worker — so a
burst of traffic degrades to "delayed" rather than "dropped."

## Module map & links

| Area | What's there | README |
|---|---|---|
| HTTP routes | Every webhook + QR + widget endpoint | [src/api/README.md](src/api/README.md) |
| Conversation engine | FSM states, handlers, abuse/go-back logic | [src/fsm/README.md](src/fsm/README.md) |
| NLU / LLM | Language detection, intent routing, Ollama tasks | [src/nlu/README.md](src/nlu/README.md) |
| QR check-in | Per-doctor, hospital-wide, and pre-registration flows | [src/qr/README.md](src/qr/README.md) |
| Evolution API bridge | WhatsApp-gateway auto-responder (nudge to web widget) | [src/evolution/README.md](src/evolution/README.md) |
| Web booking widget | Browser-based book/cancel/reschedule pages | [src/whatsapp_web/README.md](src/whatsapp_web/README.md) |
| Data layer | All repositories (booking, scheduling, notifications, etc.) | [src/repositories/README.md](src/repositories/README.md) |
| Runtime/reliability + multi-channel delivery | Turn queue, guards, dedup, Kafka bridges, SMS, send logic | [src/runtime/README.md](src/runtime/README.md) |
| Background jobs | Doctor reminders, notification dispatch, cleanup | [src/automation/README.md](src/automation/README.md) |
| Voice prototype (experimental) | Standalone Whisper transcription app — not wired into `main.py` | [src/live_whisper/README.md](src/live_whisper/README.md) |
| Test suite | ~140 tests; naming conventions and what each group verifies | [tests/README.md](tests/README.md) |

## Project structure

```text
.
├── main.py                        # FastAPI app, wiring, lifespan, turn processing
├── requirements.txt
├── .env / .env.example
├── db_automation_migration.sql    # schedule rebuild queue + triggers
├── db_multi_channel_migration.sql # channel_accounts / doctor_channel_bindings
├── sql_trigger_log.sql            # doctor-cancel/reschedule notification triggers
├── migrate_multi_channel_schema.py
├── Dockerfile / docker-compose.yml
├── src/
│   ├── config.py                  # Settings dataclass, all env vars
│   ├── db_store.py                # repository factory functions (from_env)
│   ├── session_store.py           # SessionManager: FSM <-> Redis/MySQL
│   ├── chat_logger.py             # structured per-chat event logging
│   ├── timezone_utils.py
│   ├── ollama_runtime.py          # Ollama readiness helper (currently disabled at startup)
│   ├── appointment_fsm.py         # DEAD — legacy re-export shim, unused
│   ├── api/                       # HTTP route registration — see src/api/README.md
│   ├── automation/                # scheduler.py — see src/automation/README.md
│   ├── db/
│   │   └── connection.py          # MySQL connection pooling helper
│   ├── evolution/                 # Evolution API client/policy — see src/evolution/README.md
│   ├── fsm/                       # conversation engine — see src/fsm/README.md
│   ├── live_whisper/              # experimental voice prototype — see src/live_whisper/README.md
│   ├── llm/                       # Ollama client + prompt tasks — see src/nlu/README.md
│   ├── messages/
│   │   └── templates.py           # localized (EN/HI/Hinglish) message strings
│   ├── nlu/                       # language/intent/extraction — see src/nlu/README.md
│   ├── qr/                        # QR check-in — see src/qr/README.md
│   ├── repositories/              # data layer — see src/repositories/README.md
│   ├── runtime/                   # queue/guard/delivery/Kafka — see src/runtime/README.md
│   └── whatsapp_web/              # booking-widget HTML renderer — see src/whatsapp_web/README.md
└── tests/                         # ~140 files — see tests/README.md
```

## Database

- `DATABASE_URL` must be a `mysql+mysqlconnector://...` URL. MySQL is not
  containerized by this repo's `docker-compose.yml` — it's expected to be an
  external/managed instance.
- Schemas are ensured (created/altered if missing) at startup by each
  repository: booking/notification schema, conversation/dedup/overflow
  schema, QR schema, doctor-cache-invalidation schema, multi-channel schema.
- See [src/repositories/README.md](src/repositories/README.md) for what
  each repository owns, and the three root-level `.sql` files for manual
  migrations (trigger-based notification queues, slot dedup, multi-channel
  tables).

## Redis

Optional everywhere — the app fails open (falls back to in-process
behavior or direct DB reads) if Redis is unreachable. Used for:

- Session snapshots (`<prefix>:sess:<scoped_user_id>`)
- Per-user processing lock + busy hints (`<prefix>:proc:*`, `<prefix>:busy:*`)
- Doctor availability cache (`<prefix>:avail:*`) and hospital QR options cache
- Multi-channel routing cache (versioned by `route_cache_versions`)
- Evolution auto-response session-window counters

Configured via `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`,
`REDIS_KEY_PREFIX`.

## Deployment (Docker)

`docker-compose.yml` defines:

- `app` — built from `Dockerfile` (`python:3.10-slim`, runs
  `uvicorn main:app`), host networking.
- `redis` — `redis:7-alpine`.
- `kafka` + `kafka-ui` — single-node KRaft Kafka broker (`apache/kafka:3.8.0`)
  and a web UI for inspecting it. `app` depends on Kafka being healthy, so
  Kafka is on by default in this compose file even though the code itself
  treats it as optional (see [src/runtime/README.md](src/runtime/README.md)).
- `evolution-api` — the self-hosted `evoapicloud/evolution-api` gateway
  backing the Evolution channel.

**Not containerized**: MySQL (external/managed) and Ollama (run on a host
machine, GPU or CPU, via `ollama serve`; see `scripts/start_ollama_gpu_host.sh`).

## Environment variables

Grouped by the module that owns them — full authoritative list is
`src/config.py`.

### Core
`APP_NAME`, `LOG_LEVEL`, `DATABASE_URL`, `ENABLE_DB_BOOKING`,
`SESSION_TTL_MINUTES`, `MAX_MESSAGE_CHARS`, `MIXED_RESPONSE_LANGUAGE`

### LLM / Ollama — see [src/nlu/README.md](src/nlu/README.md)
`LLM_PROVIDER`, `LLM_MODEL_NAME`, `OLLAMA_BASE_URL`, `LLM_TIMEOUT_SECONDS`,
`ENABLE_LLM_POLISH`, `OLLAMA_AUTO_START`, `OLLAMA_AUTO_PULL`,
`OLLAMA_STARTUP_TIMEOUT_SECONDS`, `OLLAMA_MAX_CONCURRENCY`

### WhatsApp (Twilio / Meta / Infobip) & Telegram — see [src/api/README.md](src/api/README.md), [src/runtime/README.md](src/runtime/README.md)
`WHATSAPP_PROVIDER`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_WHATSAPP_NUMBER`, `ENABLE_TWILIO_SIGNATURE_VALIDATION`,
`TWILIO_TEMPLATE_*_SID`, `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_BUSINESS_ACCOUNT_ID`, `META_APP_SECRET`,
`ENABLE_META_SIGNATURE_VALIDATION`, `WHATSAPP_GRAPH_API_VERSION`,
`META_WHATSAPP_VERIFY_TOKEN`, `INFOBIP_API_KEY`, `INFOBIP_BASE_URL`,
`INFOBIP_WHATSAPP_NUMBER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_BOT_USERNAME`, `CHANNEL_ROUTING_STRICT`,
`ROUTE_CACHE_INVALIDATE_TOKEN`, `ADMIN_API_KEY`

### Queue / runtime — see [src/runtime/README.md](src/runtime/README.md)
`QUEUE_WORKER_COUNT`, `QUEUE_MAX_SIZE`, `QUEUE_RETRY_ATTEMPTS`,
`PROCESSING_TIMEOUT_SECONDS`, `QUEUE_OVERFLOW_REQUEUE_ATTEMPTS`,
`QUEUE_OVERFLOW_REQUEUE_BACKOFF_SECONDS`, `PER_USER_QUEUE_MAX`,
`PER_USER_COALESCE_WINDOW_SECONDS`, `USER_LOCK_STRIPES`,
`INBOUND_SID_RETENTION_DAYS`, `INBOUND_SID_PURGE_INTERVAL_SECONDS`

### Kafka (optional) — see [src/runtime/README.md](src/runtime/README.md)
`KAFKA_ENABLED`, `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TURN_TOPIC`,
`KAFKA_TURN_CONSUMER_GROUP`, `KAFKA_POLL_TIMEOUT_MS`,
`KAFKA_NOTIFICATION_TOPIC`, `KAFKA_NOTIFICATION_CONSUMER_GROUP`

### Redis
`REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `REDIS_KEY_PREFIX`,
`REDIS_PROCESSING_TTL_SECONDS`, `REDIS_BUSY_HINT_TTL_SECONDS`,
`REDIS_DOCTOR_CACHE_TTL_SECONDS`, `REDIS_HOSPITAL_CACHE_TTL_SECONDS`

### Automation / reminders — see [src/automation/README.md](src/automation/README.md)
`AUTOMATION_ENABLED`, `DOCTOR_REMINDER_ENABLED`,
`DOCTOR_REMINDER_INTERVAL_SECONDS`, `DOCTOR_REMINDER_LEAD_MINUTES`,
`DOCTOR_REMINDER_LEAD_MINUTES_LIST`, `DOCTOR_REMINDER_WINDOW_SECONDS`,
`REPORT_RETENTION_DAYS`, `REPORT_MAX_FILES`, `REPORT_CLEANUP_INTERVAL_SECONDS`

### Evolution API — see [src/evolution/README.md](src/evolution/README.md)
`EVOLUTION_API_BASE_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_WEBHOOK_URL`,
`EVOLUTION_WEBHOOK_SECRET`, `EVOLUTION_SEND_TEXT_PATH_TEMPLATE`,
`EVOLUTION_BOOKING_BASE_URL`, `EVOLUTION_BOOKING_PATH_PREFIX`,
`EVOLUTION_SESSION_WINDOW_SECONDS`, `EVOLUTION_WELCOME_TEMPLATE`,
`EVOLUTION_WARNING_TEXT`

### SMS — see [src/runtime/README.md](src/runtime/README.md)
`SMS_ENABLED`, `SMS_ENABLED_CHANNELS`, `SMS_API_URL`, `SMS_API_KEY`,
`SMS_SENDER`, `SMS_MESSAGE_TYPE`, `SMS_RESPONSE`, `SMS_BASE_URL`,
`X_INTERNAL_API_KEY`

### QR / web widget — see [src/qr/README.md](src/qr/README.md), [src/whatsapp_web/README.md](src/whatsapp_web/README.md)
`QR_OVERFLOW_EXTENSION_MINUTES`, `FRONTEND_BASE_URL`

## Run & test

Install:
```bash
pip install -r requirements.txt
```

Run app:
```bash
uvicorn main:app --reload
```

Run all tests:
```bash
pytest -q
```

Run focused FSM suites:
```bash
pytest -q tests/test_fsm_flow_individual.py tests/req_001_fsm_state_transitions.py tests/req_005_known_unknown_patient.py tests/req_008_go_back_press_0.py tests/req_009_check_availability_flow.py
```

See [tests/README.md](tests/README.md) for how the ~140 test files are
organized and what each naming convention (`req_0xx_*`, `test_prod_pointN_*`,
`test_hard_*`, `integration_*`, `live_*`/`demo_*`) means.

## Data/artifacts written at runtime

- `data/seen_message_sids.jsonl` — inbound dedup store
- `data/doctor_reminder_keys.jsonl` — reminder dedup store
- `data/reports/` — xlsx reminder patient lists (cleaned by scheduler policy)
- `logs/` — application logs (mounted volume in Docker)

## Known dead / experimental code

- **`src/appointment_fsm.py`** — a 5-line re-export shim for backward
  compatibility. Nothing in the codebase imports it anymore; the live FSM is
  `src/fsm/appointment_fsm.py`. Safe to ignore or remove.
- **Live Whisper voice/prescription transcription** — a working prototype
  (`tests/live_whisper_browser_stream.py`, GPU launch scripts, opt-in mic
  tests) but it is a **separate standalone FastAPI app on its own port**,
  never imported by `main.py`. See
  [src/live_whisper/README.md](src/live_whisper/README.md) before assuming
  it's part of the live bot.
