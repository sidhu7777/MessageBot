# Message Bot (WhatsApp + Telegram Appointment Assistant)

FastAPI service for clinic appointment booking with:
- FSM-based conversation flow
- Ollama-backed LLM intent/extraction
- Async per-user turn queue
- MySQL persistence (sessions, booking, notification queues)
- Redis-assisted locks/cache (optional but recommended)
- Doctor reminder scheduler (T-60 and T-10 by default)

## Current Runtime Entry

- App entrypoint: `main.py`
- Run command:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

- `GET /health`
- `GET /health/queue`
- `POST /webhook` (Twilio WhatsApp inbound)
- `POST /telegram/webhook` (Telegram inbound)
- `POST /twilio/status` (delivery callbacks)

## Tech Stack

- Python
- FastAPI + Uvicorn
- Twilio SDK
- MySQL (`mysql-connector-python`)
- Redis (`redis`)
- openpyxl (doctor report XLSX)
- dotenv

Dependencies are in `requirements.txt`.

## Project Structure (Current)

```text
.
├── main.py
├── requirements.txt
├── .env
├── README.md
├── db_automation_migration.sql
├── sql_trigger_log.sql
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── db_store.py
│   ├── appointment_fsm.py
│   ├── ollama_runtime.py
│   ├── session_store.py
│   ├── api/
│   │   └── __init__.py
│   ├── automation/
│   │   ├── __init__.py
│   │   └── scheduler.py
│   ├── db/
│   │   └── connection.py
│   ├── fsm/
│   │   ├── __init__.py
│   │   └── appointment_fsm.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── tasks.py
│   ├── messages/
│   │   ├── __init__.py
│   │   └── templates.py
│   ├── nlu/
│   │   ├── __init__.py
│   │   ├── extractors.py
│   │   ├── initial_router.py
│   │   └── language_detector.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── booking_repository.py
│   │   ├── conversation_repository.py
│   │   └── scheduling_repository.py
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── message_sid_store.py
│   │   ├── turn_queue.py
│   │   ├── user_processing_guard.py
│   │   └── user_turn_buffer.py
│   └── sql/
│       └── doctor_cache_invalidation_setup.sql
└── tests/
    ├── (multiple pytest and requirement/diagnostic test files)
```

## Architecture Summary

1. Webhook receives inbound message.
2. Fast dedup check (`data/seen_message_sids.jsonl`).
3. Per-user processing lock (`UserProcessingGuard`).
4. Turn queued to `TurnQueueProcessor`.
5. Worker loads FSM from `SessionManager`.
6. FSM handles message (LLM used where configured).
7. Reply sent via Telegram/Twilio.
8. FSM/session saved to Redis + MySQL (if configured).

Additional background workers:
- Overflow turn poller (MySQL `inbound_turn_queue`)
- Doctor cache invalidation poller (`doctor_cache_invalidation_queue`)
- Reminder scheduler loop

## Database (MySQL) Usage

Connection source:
- `DATABASE_URL` must be `mysql+mysqlconnector://...`

Key repositories:
- `BookingRepository`: booking flow, appointment notifications, delivery status, reminder data
- `ConversationRepository`: session snapshots, inbound dedup table, overflow turn queue
- `SchedulingRepository`: availability lookups, cache invalidation queue + trigger wiring

Schemas are ensured at startup:
- `booking_repository.ensure_notification_schema()`
- `conversation_repository.ensure_schema()`
- `scheduling_repository.ensure_cache_invalidation_schema()`

## Redis Usage

Redis is optional; app can run fail-open without it.

Used for:
- Session snapshots (`<REDIS_KEY_PREFIX>:sess:<user_id>`)
- Per-user processing lock/busy hints (`<prefix>:proc:*`, `<prefix>:busy:*`)
- Doctor availability cache (`<prefix>:avail:*`)

Configured from:
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`
- `REDIS_KEY_PREFIX`

## Environment Variables (Important)

### Core
- `APP_NAME`
- `LOG_LEVEL`
- `DATABASE_URL`

### LLM / Ollama
- `LLM_PROVIDER` (default `ollama`)
- `LLM_MODEL` (default `qwen3:0.6b`)
- `OLLAMA_BASE_URL`
- `LLM_TIMEOUT_SECONDS`
- `OLLAMA_AUTO_START`
- `OLLAMA_AUTO_PULL`
- `OLLAMA_STARTUP_TIMEOUT_SECONDS`

### Channel Credentials
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_NUMBER`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET` (optional)
- `TELEGRAM_BOT_USERNAME` (fallback; runtime also resolves with `getMe`)

### Queue / Runtime
- `QUEUE_WORKER_COUNT`
- `QUEUE_MAX_SIZE`
- `QUEUE_RETRY_ATTEMPTS`
- `PROCESSING_TIMEOUT_SECONDS`
- `QUEUE_OVERFLOW_REQUEUE_ATTEMPTS`
- `QUEUE_OVERFLOW_REQUEUE_BACKOFF_SECONDS`
- `PER_USER_QUEUE_MAX`
- `PER_USER_COALESCE_WINDOW_SECONDS`
- `USER_LOCK_STRIPES`

### Redis
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`
- `REDIS_KEY_PREFIX`
- `REDIS_PROCESSING_TTL_SECONDS`
- `REDIS_BUSY_HINT_TTL_SECONDS`
- `REDIS_DOCTOR_CACHE_TTL_SECONDS`

### Automation / Reminder
- `AUTOMATION_ENABLED`
- `DOCTOR_REMINDER_ENABLED`
- `DOCTOR_REMINDER_INTERVAL_SECONDS`
- `DOCTOR_REMINDER_LEAD_MINUTES`
- `DOCTOR_REMINDER_LEAD_MINUTES_LIST` (e.g. `60,10`)
- `DOCTOR_REMINDER_WINDOW_SECONDS`
- `REPORT_RETENTION_DAYS`
- `REPORT_MAX_FILES`
- `REPORT_CLEANUP_INTERVAL_SECONDS`

## SQL Files Present

- `db_automation_migration.sql`
  - schedule rebuild queue + triggers for `doctor_clinic_schedule`
  - slot dedup cleanup + unique constraint
- `sql_trigger_log.sql`
  - doctor-cancel / doctor-reschedule notification triggers on `appointment`
- `src/sql/doctor_cache_invalidation_setup.sql`
  - stored procedure to create invalidation queue and related triggers

## Run & Test

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

## Data/Artifacts Written at Runtime

- `data/seen_message_sids.jsonl`
- `data/doctor_reminder_keys.jsonl`
- `reports/` (xlsx reminder outputs; cleaned by scheduler policy)

## Notes

- Service supports both Telegram and WhatsApp paths from the same FSM.
- Turn processing is asynchronous and per-user serialized.
- Timeout-safe message can appear before final reply when LLM processing is slow.
