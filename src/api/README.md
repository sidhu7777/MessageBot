<!-- src/api/README.md -->
# HTTP Route Layer

This directory contains every FastAPI route the clinic-appointment bot exposes: inbound
messaging webhooks for four channel providers (Twilio WhatsApp, Meta WhatsApp Cloud API,
Infobip WhatsApp, Telegram), the QR check-in / registration web flows, a WhatsApp-styled
HTML/JS self-service booking widget, and the Evolution API (WhatsApp Web bridge)
auto-responder. None of these files use `@app.get`/`@app.post` decorators directly at
import time — each module exposes a `register_*_routes(app, ...)` function that `main.py`
calls once at startup, passing in already-constructed dependencies (repositories,
settings, the logger, the turn processor, etc.), several of them as zero-arg lambdas so
the route closures always read the *current* module-level singleton rather than a stale
reference captured at import time. This document walks through each file's routes and the
mechanics behind them.

Back to [root README](../../README.md).

---

## Files in this directory

| File | Registers |
|---|---|
| `webhooks.py` | `register_webhook_routes` — Twilio, Meta Cloud API, Infobip, Telegram inbound + status webhooks, plus the route-cache admin endpoint |
| `qr_routes.py` | `register_qr_routes` — per-doctor and hospital-wide QR check-in, QR image generation, hospital pre-registration |
| `whatsapp_web_routes.py` | `register_whatsapp_web_routes` — the plain HTML/JS "WhatsApp Web" booking widget |
| `evolution_webhook_routes.py` | `register_evolution_webhook_routes` — Evolution API (unofficial WhatsApp Web bridge) auto-responder |
| `__init__.py` | empty (`__all__ = []`); the package exists only to hold these modules |

All four `register_*_routes` calls happen in `MessageBot/main.py` after the FastAPI `app`
object and every singleton dependency (session manager, repositories, Redis client, turn
processor, etc.) have been constructed. See the "Wiring from `main.py`" section at the end
of this document for exactly what gets injected where.

---

## `webhooks.py`

### Route table

| Method | Path | Purpose |
|---|---|---|
| POST | `{twilio_inbound_path}` (default `/webhook`, or `settings.twilio_webhook_url`) | Twilio WhatsApp inbound message |
| GET | `{whatsapp_webhook_path}` (default `/whatsapp/webhook`) | Meta Cloud API verification handshake |
| POST | `{whatsapp_webhook_path}` | Meta Cloud API inbound message + delivery status callback |
| POST | `{infobip_webhook_path}` (default `/infobip/webhook`) | Infobip WhatsApp inbound message + delivery status callback |
| POST | `{telegram_inbound_path}` (default `/telegram/webhook`) | Legacy (non-scoped) Telegram webhook |
| POST | `{telegram_inbound_path}/{webhook_key}` | Per-account Telegram webhook |
| POST | `/twilio/status` | Twilio delivery-status callback |
| POST | `/internal/route-cache/invalidate` | Admin: bump the channel-routing cache version |

All of the configurable paths (`twilio_inbound_path`, `telegram_inbound_path`,
`whatsapp_webhook_path`, `infobip_webhook_path`) are derived by `_route_path()`, which
takes either a bare path or a full URL from `settings.*_webhook_url` and normalizes it to
a path (stripping scheme/host, ensuring a leading slash, trimming a trailing slash),
falling back to the hardcoded default when the setting is empty.

### Multi-account routing resolution — `_resolve_bound_context`

Because a single deployment can host many doctors/clinics behind many WhatsApp numbers
and many Telegram bots, every inbound handler must first figure out *which*
`channel_account` (and therefore which `doctor_id`/`admin_id`) the message belongs to
before it can be handed to the FSM. That resolution is centralized in
`_resolve_bound_context(channel, account_identity="", webhook_key="", webhook_secret="")`,
which returns a 3-tuple `(scoped_user_prefix, bot_identity, route_context)` — an empty
tuple of `("", "", {})` means routing could not be resolved.

Resolution logic:
- For `channel == "telegram"` with a `webhook_key`, it calls
  `channel_account_repository.resolve_by_webhook_key(channel="telegram", webhook_key=..., webhook_secret=...)`.
- For `channel == "whatsapp"` with an `account_identity` (the `To` number / phone-number-id
  the message arrived on), it calls
  `channel_account_repository.resolve_by_sender_identity(channel="whatsapp", sender_identity=...)`.
- If an `account` is found, it then calls `repo.resolve_binding(account.channel_account_id)`
  to get the bound `doctor_id`/`admin_id`. If either lookup fails or returns nothing,
  resolution fails (empty tuple).
- On success it builds `route_ctx = {channel_account_id, doctor_id, admin_id, channel_provider}`,
  a `bot_identity` string (for Telegram: `telegram_username:<username>`; for WhatsApp: the
  raw `sender_identity`), and a `scoped_prefix` of the form `acct:{channel_account_id}|`.
  `scoped_prefix` is not returned as `scoped_user_prefix` output alone — it's later used by
  `build_scoped_user_id()` (from `src.runtime.account_scope`) to prefix the FSM user-id so
  that two different doctors' patients with the same phone number never collide in session
  state.

**Strict routing.** `strict_routing` (from `settings.channel_routing_strict` /
`CHANNEL_ROUTING_STRICT`, default `true`) controls what happens when resolution fails: if
strict, the inbound message is dropped (logged as a warning, HTTP 200 returned so the
provider doesn't retry) instead of being processed against an unscoped/default account.

### Two-layer route cache

Because `_resolve_bound_context` would otherwise hit the DB (and Redis) on every single
inbound message, results are cached in two layers:

1. **L1 (in-process, per-worker):** `l1_cache: dict[str, tuple[float, tuple[str, str, dict]]]`
   guarded by `l1_lock`. Keyed by a cache key (see below), each entry stores an expiry
   (`time.monotonic()` + `route_l1_ttl`) and the resolved 3-tuple. TTL is
   `channel_route_l1_cache_ttl_seconds` / `CHANNEL_ROUTE_L1_CACHE_TTL_SECONDS`, minimum 5s,
   default 20s. Checked first in `_route_cache_get`; expired entries are evicted lazily.
2. **L2 (shared Redis, via `route_cache_client`):** if the L1 misses, `_route_cache_get`
   asks the injected Redis client for the same key, parses the JSON payload
   (`{"scoped_prefix", "bot_identity", "route_ctx"}`), and if found, backfills L1 before
   returning. TTL is `channel_route_cache_ttl_seconds` / `CHANNEL_ROUTE_CACHE_TTL_SECONDS`,
   minimum 15s, default 120s, applied via Redis `EX`.

Cache keys are versioned and provider-specific:
- Telegram: `_telegram_cache_key(webhook_key, webhook_secret)` →
  `"{prefix}:route:v{version}:telegram:key:{webhook_key}:sec:{sha256(secret)[:16]}"`.
- WhatsApp: `_whatsapp_cache_key(account_identity)` →
  `"{prefix}:route:v{version}:whatsapp:identity:{normalized_number}"` (normalized via
  `_normalize_whatsapp_number`, lower-cased).

The `{version}` segment is the mechanism used to invalidate every cached entry at once
without scanning/deleting individual keys: `_route_cache_version()` reads a monotonic
version counter, itself cached in-process for `channel_route_version_refresh_seconds`
(default 5s) via `l1_version`/`l1_version_lock`. When that local copy expires, it
re-resolves the authoritative version by taking the **max** of the Redis value at key
`"{prefix}:route:version"` and `channel_account_repository.current_route_cache_version()`
(a DB-backed counter), reconciling the two if they disagree (writing the higher value back
to Redis). Bumping the version — e.g. after an admin changes which doctor a WhatsApp
number is bound to — is done by `_invalidate_route_cache_version()`, which increments the
DB counter (`repo.bump_route_cache_version()`), increments the Redis counter
(`cache.incr(...)`), reconciles the two, clears the local L1 dict outright, and refreshes
the local version cache. Because every cache key embeds the current version, once the
version is bumped, all pre-existing keys become unreachable (effectively invalidated)
without needing deletion — old entries simply expire naturally via their Redis TTL.

### `/internal/route-cache/invalidate`

Protected by a bearer-style token compared against the `X-Route-Cache-Token` header (or a
`?token=` query param as fallback). The expected token,`route_invalidate_token`, is
resolved once at registration time as the first non-empty value of
`settings.route_cache_invalidate_token`, `ROUTE_CACHE_INVALIDATE_TOKEN` env var, or
`settings.admin_api_key` — i.e. it can piggyback on the general admin API key if no
dedicated token is configured. If no token is configured at all, the endpoint always
403s ("Route cache invalidation not enabled"). On a valid token it calls
`_invalidate_route_cache_version()` and returns `{"ok": true, "route_cache_version": N}`.

### Twilio WhatsApp inbound — `POST {twilio_inbound_path}` (`webhook`)

1. Parses the request as form data (`await request.form()`). Extracts `MessageSid` (or
   `SmsMessageSid` as fallback) for dedup, and the message body with a priority order:
   `ButtonPayload` → `ButtonText` → `Body` (this lets Twilio's WhatsApp quick-reply buttons
   feed their payload/text into the same pipeline as free text). `From` and `To` are read
   from the form (`To` falls back to `settings.twilio_whatsapp_from`).
2. **Signature validation.** If `settings.enable_twilio_signature_validation` is true, it
   reads the `X-Twilio-Signature` header and validates it via
   `request_validator.validate(str(request.url), dict(form), signature)` — the standard
   Twilio `RequestValidator` HMAC check (built in `main.py` from `settings.twilio_auth_token`).
   An invalid signature raises `HTTPException(403, "Invalid Twilio signature")`.
3. Resolves routing via `_resolve_bound_context("whatsapp", account_identity=to_number)`.
   If `strict_routing` and resolution failed, the message is dropped (200 OK, no processing).
4. Hands off to `_queue_whatsapp_turn(...)` (see below) with `to_number` set to the
   resolved `bot_identity` (falling back to the raw `to_number` if unresolved).

### `_queue_whatsapp_turn` — SID dedup, ACK-first buffering, and the guard/buffer/processor handoff

This is the shared engine behind Twilio, Meta, and Infobip WhatsApp inbound handling
(Telegram has its own near-identical implementation in `_telegram_webhook_impl`, described
below). Given `from_number`, `body`, `inbound_sid`, `to_number`, and an optional
`route_context`:

1. If `route_context` carries a `channel_account_id`, the user id is rewritten via
   `build_scoped_user_id(channel_account_id, from_number)` into `scoped_from_number`, and
   `set_user_route_context(scoped_from_number, route_context)` stashes the route context in
   `main.py`'s module-level `_user_route_context` dict so the turn-processing worker
   (`_process_turn` in `main.py`) can later pin the FSM's `channel_account_id`/`doctor_id`/
   `admin_id`/`channel_provider` for the *first* message of a session.
2. **SID dedup.** If `inbound_sid` is non-empty, `sid_store.seen_or_add(inbound_sid)` is
   called (a `PersistentMessageSidStore` backed by a JSONL file plus in-memory set, capped
   at 50k entries). If the SID was already seen, the handler immediately returns an empty
   200 response — this absorbs provider webhook retries so the same message is never
   processed twice.
3. `set_user_bot_identity(scoped_from_number, to_number)` records which bot/number the user
   is talking to (used later to stamp `fsm.bot_whatsapp_number`).
4. `pre_state = session_manager.get_cached_state(scoped_from_number, default="INIT")` is
   fetched purely for logging/diagnostics.
5. **ACK-first dispatch branch.** If `settings.twilio_use_rest_responses` is true *and* at
   least one outbound-capable client is configured (a Twilio REST client, or Meta Cloud API
   credentials, or Infobip credentials), the handler tries to enqueue the turn for
   asynchronous processing rather than blocking the webhook response on FSM execution:
   - `user_processing_guard.acquire(scoped_from_number)` — a Redis-backed
     (`UserProcessingGuard`) per-user lock ensuring only one turn is being processed for a
     given user at a time (protects against out-of-order replies when a user double-sends).
   - If the lock **cannot** be acquired (another turn is already in flight for this user),
     the new turn is pushed onto `user_turn_buffer` (a `UserTurnBuffer`, which coalesces
     rapid-fire messages within a collapse window and caps pending turns per user) and the
     webhook returns 200 immediately — the buffered turn will be picked up later by
     `_submit_next_buffered_turn` once the in-flight turn completes.
   - If the lock **is** acquired, a `TurnTask(from_number, body, inbound_sid, pre_state)` is
     built and submitted to `turn_processor.submit(task)` (a `KafkaTurnBridge` wrapping a
     `TurnQueueProcessor` worker pool in `main.py`). If the submit fails (`enqueued` is
     falsy, e.g. queue full), the guard is released and the task falls back to the buffer.
   - Any exception during this branch releases the guard (if acquired) and buffers the task
     as a fallback, so the webhook still returns 200 rather than raising.
   - In every case the webhook responds with an empty `PlainTextResponse("", status_code=200)`
     — Twilio/Meta/Infobip webhooks don't need a TwiML/JSON body for buffered/queued async
     processing; the actual reply is sent later out-of-band via the channel's REST API
     (handled in `main.py`'s `_send_channel_response` → `ChannelDelivery`).
6. **Non-ACK-first fallback branch** (`twilio_use_rest_responses` false or no REST client
   configured): the task is pushed straight to `user_turn_buffer`, then
   `submit_next_buffered_turn(scoped_from_number)` is called to kick off processing
   synchronously-ish through the same guard/buffer/processor path, and 200 is returned.

In short: **dedup → scope the user id → acquire per-user processing lock (or buffer) →
submit to the turn queue → FSM runs asynchronously in a worker → reply delivered via REST**.
The webhook handler itself never calls the FSM directly; `turn_processor`'s worker pool
calls `_process_turn` (defined in `main.py`), which does `session_manager.get_or_create(...)`,
pins routing context onto the FSM, and calls `fsm.handle(body)`.

### Meta WhatsApp Cloud API — `GET`/`POST {whatsapp_webhook_path}`

**GET (verification handshake).** Standard Meta webhook subscription challenge: reads
`hub.mode`, `hub.challenge`, `hub.verify_token` query params; if `mode == "subscribe"` and
`verify_token` matches `settings.whatsapp_webhook_verify_token`, echoes back the raw
`challenge` string with 200. Otherwise 403.

**POST (inbound + status).**
1. Reads the raw body bytes first (needed for signature verification, since parsing JSON
   would consume the stream).
2. **HMAC signature check.** If `settings.enable_meta_signature_validation` and
   `settings.meta_app_secret` are both set, computes
   `hmac.new(meta_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()`, prefixes it
   `sha256=`, and compares against the `X-Hub-Signature-256` header using
   `hmac.compare_digest` (constant-time). Mismatch → `HTTPException(403)`.
3. Parses the JSON body and walks the Meta webhook envelope shape:
   `payload["entry"][*]["changes"][*]["value"]`. Each `value` may contain `messages` and/or
   `statuses`.
4. **Inbound messages:** for each message, `_extract_meta_whatsapp_body(message)` extracts
   text depending on `message["type"]` — `text` (from `text.body`), `button` (from
   `button.text` or `button.payload`), or `interactive` (`button_reply.title`/`.id` or
   `list_reply.title`/`.id`). `from_number` is normalized to `whatsapp:+<digits>`, and
   `to_number`/`bot_identity` comes from `value["metadata"]["display_phone_number"]` or
   `phone_number_id`. Routing is resolved the same way as Twilio
   (`_resolve_bound_context("whatsapp", account_identity=...)`), strict-dropped if
   unresolved, then handed to the same `_queue_whatsapp_turn` used by the Twilio path — so
   Meta-sourced messages go through identical dedup/guard/buffer/processor logic (dedup key
   here is the Meta message `id`).
5. **Delivery statuses:** for each entry in `value["statuses"]`, extracts message id,
   status, recipient id, and the first error's code/title if present, resolves routing by
   the display phone/phone_number_id, and persists via
   `booking_repository.upsert_delivery_status(provider="meta", ...)` including the resolved
   `channel_account_id`/`doctor_id`/`admin_id` and the raw JSON payload for audit.
6. Always returns 200 (Meta expects an ack, not content) unless an early `_queue_whatsapp_turn`
   call itself returned a non-200 response, in which case it's propagated immediately.

### Infobip webhook — `POST {infobip_webhook_path}`

Infobip's payload puts everything under `payload["results"]`, a list of result objects that
can be either inbound messages or delivery-status reports (not both). For each result:
- `inbound_sid` comes from `messageId`/`id`/`pairedMessageId`.
- `from_number`/`to_number` are normalized via `_normalize_whatsapp_number` (which produces
  `whatsapp:+<digits>` given any raw phone-ish string).
- `_extract_infobip_text(result)` pulls text from `message.text`/`message.body` or
  `content.text`/`content.body`, falling back to top-level `text`/`body`.
- If text was extracted, it's treated as an inbound message: routing resolved via
  `_resolve_bound_context("whatsapp", account_identity=to_number)`, strict-dropped if
  unresolved, otherwise routed through `_queue_whatsapp_turn` exactly like Twilio/Meta.
- If no text was extracted but a `booking_repository` and `inbound_sid` are available, the
  result is treated as a delivery-status report: pulls `status.name`/`status.groupName`,
  `error.id`/`status.id`, `error.description`/`status.description`, and persists via
  `booking_repository.upsert_delivery_status(provider="infobip", ...)` with resolved route
  context, same shape as the Meta status handler.

### Telegram — legacy vs per-account webhook

Both routes delegate to the shared `_telegram_webhook_impl(request, webhook_key="")`:

- `POST {telegram_inbound_path}` (`telegram_webhook`) calls the impl with `webhook_key=""`
  — the **legacy, unscoped** path, intended for single-bot deployments.
- `POST {telegram_inbound_path}/{webhook_key}` (`telegram_webhook_scoped`) calls the impl
  with the path parameter — the **per-account** path, letting each Telegram bot/account be
  registered at its own webhook URL so Telegram routes each bot's updates independently.

Inside `_telegram_webhook_impl`:
1. Reads `X-Telegram-Bot-Api-Secret-Token` header into `telegram_secret`.
2. **If `webhook_key` is present:** resolves routing via
   `_resolve_bound_context("telegram", webhook_key=webhook_key, webhook_secret=telegram_secret)`.
   This is how the per-account path both identifies the account *and* authenticates the
   caller — `resolve_by_webhook_key` on the repository presumably checks the secret matches
   the account's stored secret. If `strict_routing` and resolution fails, drop (200, no
   processing).
3. **If `webhook_key` is absent** (legacy path): if `strict_routing` is on, the request is
   dropped outright (per-account webhooks are mandatory in strict mode). If not strict, it
   falls back to comparing `telegram_secret` against the single global
   `settings.telegram_webhook_secret` — a 403 `HTTPException` on mismatch (only used when
   there's no channel-account-based auth to fall back on).
4. Parses the update JSON, extracts `message` (or `edited_message`), the text, and the
   Telegram user id (`from.id` or `chat.id`). Builds `raw_from_number = f"telegram:{telegram_user_id}"`,
   then scopes it via `build_scoped_user_id` if a `channel_account_id` was resolved (same
   pattern as WhatsApp), and calls `set_user_route_context`.
5. Logs a structured `WEBHOOK_ARRIVED` event via `log_event` (keyed by `extract_chat_id`).
6. **SID dedup** uses a composite key `f"TG{from_number}:{inbound_sid}"` (Telegram's
   `message_id` is only unique per-chat, so it's namespaced by the scoped user id) checked
   against the same `sid_store`.
7. If `bot_identity` wasn't resolved via routing (legacy path), it falls back to
   `get_telegram_bot_username()` (a callable reading `main.py`'s runtime-resolved bot
   username) formatted as `telegram_username:<username>`.
8. From here the flow mirrors `_queue_whatsapp_turn`'s ACK-first branch directly (it's not
   literally shared code, but structurally identical): `set_user_bot_identity`, fetch
   `pre_state`, acquire `user_processing_guard`, submit a `TurnTask` to `turn_processor`,
   falling back to `user_turn_buffer` when the lock is busy, the queue is full, or an
   exception occurs. It also emits richer `log_event` calls (`LOCK_ACQUIRED`/`LOCK_BUSY`,
   `TURN_QUEUED`) for observability. Always returns an empty 200 response — Telegram doesn't
   require a synchronous reply body since actual replies are sent via the Bot API
   out-of-band.

### `POST /twilio/status`

Twilio's delivery-status callback (configured separately from the inbound webhook URL,
fixed at `/twilio/status` rather than derived from settings). Parses form fields
(`MessageSid`/`SmsSid`, `MessageStatus`/`SmsStatus`, `ErrorCode`, `ErrorMessage`, `To`,
`From`), logs them at `info` level, resolves route context by the `From` number (the bot's
own WhatsApp number, since this is an outbound message's status), and persists via
`booking_repository.upsert_delivery_status(provider="twilio", ...)` with the full raw form
as `payload_json`. Always returns empty 200.

---

## `qr_routes.py`

Registered via `register_qr_routes(app, *, qr_checkin_service, logger, log_event_fn)`. All
routes are mounted on an `APIRouter()` and included into `app`. Every handler guards on
`qr_checkin_service` being configured (it's `None` when `booking_repository`/
`scheduling_repository` aren't available) and returns a 503 with a localized "not
configured" message otherwise.

### Route table

| Method | Path | Purpose |
|---|---|---|
| GET | `/qr/checkin` | Per-doctor check-in page (HTML) |
| POST | `/qr/checkin/submit` | Submit per-doctor check-in — **books/queues** the patient |
| GET | `/qr/hospital/checkin` | Hospital-wide check-in page (doctor/specialization picker, HTML) |
| GET | `/qr/hospital/options` | JSON: doctors/specializations for a hospital code |
| POST | `/qr/hospital/checkin/submit` | Submit hospital-wide check-in — **books/queues** the patient |
| POST | `/qr/generate` | Generate a per-doctor QR code (JSON: preview + download link) |
| GET | `/qr/generate/download` | Download the per-doctor QR as an SVG file |
| POST | `/qr/hospital/generate` | Generate a hospital-wide QR code (JSON) |
| GET | `/qr/hospital/generate/download` | Download the hospital-wide QR as an SVG file |
| GET | `/qr/hospital/registration/checkin` | Hospital **pre-registration** page (HTML, no booking) |
| POST | `/qr/hospital/registration/submit` | Submit pre-registration — stores patient + daily token, does **not** book |
| POST | `/qr/hospital/registration/generate` | Generate a pre-registration QR code (JSON) |
| GET | `/qr/hospital/registration/generate/download` | Download the pre-registration QR as an SVG file |

### Language resolution

Shared by every route via `_resolve_effective_language(request, payload)`, which returns
`(lang, lock_language)`:
1. `?lang=` query param, if it normalizes to one of `en`/`hi`/`hinglish` (via
   `_normalize_lang`, which treats any `hi*` prefix as `hi` and any `en*` prefix as `en`) —
   if present, `lock_language=True` (the page is told to stop auto-detecting/switching
   language because the user explicitly chose one).
2. Otherwise `payload.get("detected_language")` (submitted forms can carry a client-detected
   language), restricted to `en`/`hi` (no `hinglish` from this source).
3. Otherwise the `Accept-Language` header, parsed by `_lang_from_accept_header` (splits on
   `,`, strips `;q=...` weights, takes the first token that normalizes to `en` or `hi`).
4. Falls back to `"en"` with `lock_language=False`.

`lock_language` is passed through to the HTML renderers (`render_qr_page_html`,
`render_hospital_qr_page_html`) so the client-side JS knows whether to keep re-detecting
language from user input or respect the explicit choice.

### Per-doctor check-in (`/qr/checkin`, `/qr/checkin/submit`)

**GET `/qr/checkin?doctor_id=&clinic_id=`** — requires both `doctor_id` and `clinic_id`
(400 if missing). Looks up display names via
`qr_checkin_service.resolve_doctor_and_clinic(doctor_id, clinic_id)`, run in a thread pool
with a bounded timeout (`QR_PAGE_LOOKUP_TIMEOUT_SECONDS`, default 2.0s — falls back to
generic "Doctor"/"Clinic" placeholder names on timeout/error rather than failing the page).
Renders `render_qr_page_html(...)` — a full check-in form.

**POST `/qr/checkin/submit`** — accepts either JSON or form-encoded body (tries `request.json()`
first, falls back to `request.form()`). Extracts `patient_name`, `phone_number`,
`doctor_id`, `clinic_id` (from payload or query params). Invalid/missing `doctor_id`/
`clinic_id` → 400. On success, calls
`qr_checkin_service.process_checkin(doctor_id, clinic_id, patient_name, phone, language)`
in a thread pool — this is the **actual booking/queueing call**: the service is expected to
create or attach the patient and either book/queue them for same-day walk-in check-in.
`result.status` of `booked`, `overflow`, or `active_booking` maps to HTTP 200; anything else
(e.g. an error status) maps to 400. Structured events (`QR_SUBMIT_RECEIVED`,
`QR_SUBMIT_SUCCEEDED`/`QR_SUBMIT_FAILED`) are logged via `log_event_fn`.

**Response format switch.** After processing, the handler inspects the `Accept` header:
if it contains `text/html` and does **not** contain `application/json`, it re-renders the
full HTML check-in page (`render_qr_page_html`) with the result message/status embedded
(for non-JS/plain browser form posts); otherwise it returns a JSON body with `status`,
`message`, `detail` (duplicate of message for compatibility), `booking_id`,
`appointment_date`, `appointment_time`, `queue_position`, `estimated_time`, `clinic_name`,
`doctor_name`, and `response_language`. The same `Accept`-header switch pattern is repeated
in the hospital check-in submit handler.

### Hospital-wide check-in (`/qr/hospital/checkin`, `/qr/hospital/options`, `/qr/hospital/checkin/submit`)

This flow lets a single QR code serve an entire hospital: the patient picks a
specialization/doctor on the page itself rather than the QR encoding a specific doctor.

- **GET `/qr/hospital/checkin?hospital_code=`** (or `hospital_group_code`, either query
  param accepted) — calls `qr_checkin_service.hospital_qr_options(code)` (thread pool, longer
  timeout — `max(qr_page_lookup_timeout_seconds, 15.0)` — since this enumerates all doctors)
  to get `{hospital_code, specializations, doctors}`, falling back to an empty options dict
  on failure, and renders `render_hospital_qr_page_html(...)`.
- **GET `/qr/hospital/options`** — the same `hospital_qr_options(code)` lookup exposed as a
  raw JSON endpoint (used by the page's client-side JS to repopulate the doctor dropdown
  when the specialization filter changes, without a full page reload).
- **POST `/qr/hospital/checkin/submit`** — reads `hospital_code`, `patient_name`,
  `phone_number`, `doctor_id` from JSON/form body. Unlike the per-doctor submit, it first
  resolves a **clinic** for the chosen doctor via
  `qr_checkin_service.resolve_hospital_qr_schedule(hospital_code, doctor_id)` (the hospital
  flow doesn't know the clinic up front) — if no schedule is found, returns 400
  ("No valid doctor schedule is available..."). It then calls the same
  `qr_checkin_service.process_checkin(doctor_id=schedule.doctor_id, clinic_id=schedule.clinic_id, ...)`
  as the per-doctor flow. Response shape adds `clinic_id`, `doctor_id`, `specialization`,
  and `hospital_code` on top of the per-doctor JSON shape. Same `Accept`-header HTML/JSON
  switch as above.

### QR image generation/download

- **POST `/qr/generate`** and **POST `/qr/hospital/generate`**: resolve display names (via
  `qr_checkin_service.resolve_doctor_and_clinic` or the hospital equivalent
  `_resolve_hospital_display_name` → `qr_checkin_service.hospital_qr_display_name`), build
  the target booking URL (`build_qr_booking_url` / `build_qr_hospital_url` from
  `src.qr.generator_service`, using `request.base_url` as the origin), render an SVG QR
  code via `build_qr_svg(url=...)` (run in thread pool — QR generation is CPU-bound), and
  return JSON containing `mime_type: "image/svg+xml"`, a suggested `filename`
  (`build_download_filename`/`build_hospital_download_filename`), a `preview_data_url`
  (base64 `data:` URI via `svg_to_data_url`, for inline `<img>` preview), and a
  `download_path` pointing at the paired download route.
- **GET `/qr/generate/download`** and **GET `/qr/hospital/generate/download`** and the
  registration equivalent: regenerate the same SVG (not cached — QR generation is cheap)
  and return it as a raw `Response` with `media_type="image/svg+xml"` and a
  `Content-Disposition: attachment; filename="..."` header, i.e. a direct browser download.

### Hospital pre-registration flow (`/qr/hospital/registration/*`) — does NOT book

This is explicitly called out in the source as isolated from the booking flow above (see
the module-level comment block at the top of `qr_routes.py`). It exists for hospitals that
want patients to pre-register (capture demographic info) at a kiosk/QR without creating an
appointment.

- **GET `/qr/hospital/registration/checkin?hospital_code=&hospital_name=`** — reuses
  `qr_checkin_service.hospital_qr_options(code)` to populate the doctor/specialization
  pickers (same data source as the booking hospital page), but renders a different template,
  `render_hospital_registration_page_html`. The hospital display name resolution order is:
  explicit `hospital_name` query param → `options["hospital_name"]` from the DB → the
  hardcoded fallback constant `REGISTRATION_HOSPITAL_NAME_DEFAULT = "Nirmal Ashram Hospital"`.
- **POST `/qr/hospital/registration/submit`** — requires `patient_name`, `age`, `gender`,
  `phone_number` (400 if any missing); `doctor_id` is explicitly **optional** — if omitted
  or invalid it's coerced to `0`, and the comment in code notes this is stored as NULL
  (no doctor association) rather than rejected. Calls
  `qr_checkin_service.register_hospital_patient(hospital_code, doctor_id, patient_name, phone, age, gender)`.
  Per the module comment, this **upserts the patient** (matched by `admin_id` + name +
  phone) and stores a **daily-unique token** in `patients.tmpregtoken` — one registration
  per patient per day — but does not touch the appointments table at all. Returns the
  service's result dict directly as JSON (includes the generated `token`); `status == "error"`
  maps to 400.
- **POST `/qr/hospital/registration/generate`** / **GET `.../generate/download`** — same QR
  image generation/download pattern as above, but pointing the QR at
  `build_qr_hospital_registration_url(...)` (a distinct URL scheme for the registration
  page rather than the booking page).

---

## `whatsapp_web_routes.py`

**This is a plain HTML/JavaScript self-service widget — no WhatsApp, Twilio, Meta, or
Telegram involvement at all.** It's a browser-based booking UI (`/whatsapp/web`) styled to
look like a WhatsApp chat, but it talks directly to the backend over regular HTTP/JSON.
Critically, it **bypasses the conversational FSM entirely**: every route in this file calls
`booking_repository`/`scheduling_repository` methods directly rather than going through
`session_manager`/`fsm.handle()`. There is no session state, no turn queue, no dedup SID
store — each request is handled synchronously and statelessly against the DB.

Registered via `register_whatsapp_web_routes(app, *, booking_repository, scheduling_repository, logger)`.

### Route table

| Method | Path | Purpose |
|---|---|---|
| GET | `/whatsapp/web?doctor_id=` | Render the booking widget page for a doctor |
| GET | `/whatsapp/web/{doctor_slug}` | Same page, resolved by a doctor's URL slug instead of numeric id |
| GET | `/whatsapp/web/clinics?doctor_id=` | List clinics for a doctor |
| GET | `/whatsapp/web/dates?doctor_id=&clinic_id=` | List available appointment dates |
| GET | `/whatsapp/web/times?doctor_id=&clinic_id=&slot_date=&period=&appointment_id=&reschedule=` | List available time slots (booking or reschedule mode) |
| POST | `/whatsapp/web/lookup` | Find a caller's existing active appointments |
| POST | `/whatsapp/web/book` | Book a new appointment |
| POST | `/whatsapp/web/cancel` | Cancel an appointment |
| POST | `/whatsapp/web/reschedule` | Reschedule an appointment to a new date/time |

### Page rendering

`_render_whatsapp_web_page(request, doctor_id)` (shared by both GET page routes) requires
`doctor_id`; 400 if missing, 503 if repositories aren't configured. Resolves `admin_id` via
a raw SQL lookup `_resolve_admin_id` (`SELECT admin_id FROM doctors WHERE doctor_id = %s`,
using `booking_repository._connect()` directly — this module reaches into the repository's
private connection helper rather than going through a public repository method for several
of its lookups), then the doctor's display name via
`booking_repository.get_doctor_display_name(doctor_id, admin_id)`, and renders
`render_whatsapp_web_page_html(doctor_id, doctor_name, language, lock_language)`.
`/whatsapp/web/{doctor_slug}` additionally resolves the numeric `doctor_id` from a slug via
`_resolve_doctor_id_by_slug` (`SELECT doctor_id FROM doctors WHERE TRIM(slug) = %s`),
404ing if the slug doesn't match any doctor.

### Discovery endpoints (clinics / dates / times)

- **`/whatsapp/web/clinics`** calls `scheduling_repository.list_clinics_for_doctor(doctor_id, admin_id, 20)`
  and returns `{doctor_id, language, clinics: [{clinic_id, clinic_name, location}, ...]}`.
- **`/whatsapp/web/dates`** calls `scheduling_repository.list_available_dates(doctor_id, clinic_id, admin_id, 14)`
  (14-day lookahead) and returns `{dates: [...]}`.
- **`/whatsapp/web/times`** has two modes:
  - **Reschedule mode** (`reschedule=1` and `appointment_id` given): calls
    `_list_reschedule_times`, which fetches raw available times via
    `scheduling_repository._db_list_available_times_for_date` (again, a private method
    accessed directly) and filters out any time that would collide with another existing
    appointment for the same doctor/date via `_reschedule_conflict_exists` (a direct SQL
    query against the appointment table excluding the appointment being rescheduled).
  - **Normal booking mode**: calls `scheduling_repository.list_available_times(doctor_id, clinic_id, slot_date, admin_id, 60)`.
  - Both modes funnel through `_grouped_time_payload`, which normalizes raw `HH:MM[:SS]`
    strings, and — if there are more than 4 distinct hours and no `period` filter was
    requested — collapses them into coarse `morning`/`afternoon`/`evening` period buckets
    (`_hour_to_period`, localized labels via `_period_label` for `en`/`hi`/`hinglish`)
    rather than listing every slot; once a `period` is chosen (or there are ≤4 distinct
    hours), it returns individual hour-bucketed slots with human-readable ranges
    (`_format_time`). This is a UX affordance to avoid showing a huge flat list of times.

### Identity checks: self vs someone-else

Both `/whatsapp/web/lookup` and `/whatsapp/web/book` accept a `booking_for_self: bool`
flag and enforce name/phone consistency rules to prevent identity confusion when one phone
number is used to book for multiple family members:

- `_find_self_name_by_phone_number(phone_number, doctor_id, admin_id)` queries the
  `patients` table for a row with `profile_type = 'SELF'` matching the phone (tries several
  phone formats — bare digits, `+`-prefixed, `whatsapp:+`-prefixed, and Indian 10↔91+10
  digit variants — via `_add_candidate`/`phone_candidates`), returning the registered
  "self" name for that number, if any.
- `_self_name_mismatch(...)`: if `booking_for_self=True` and the submitted `patient_name`
  doesn't match the known self-name on file for that phone, returns the known name (used to
  build an error like *"This phone number is linked to self name X. Use that name for Self,
  or choose Someone Else."*, 400).
- `_other_name_matches_self_name(...)`: the inverse check — if `booking_for_self=False` but
  the submitted name matches the phone's registered self-name, rejects with *"Please use a
  different name other than self when booking for another person."* (400) — prevents
  someone from booking "for someone else" using their own identity to dodge the active
  booking cap (see below).
- `_find_same_day_identity_appointment(...)`: checks whether the same patient
  (name+phone, exact match, case/whitespace-normalized) already has an active appointment
  (`status IN ('BOOKED','PENDING','CONFIRMED')`) with this doctor on the target date; used
  both in `/lookup` (surfaced to the top of the returned appointment list) and `/book`
  (blocks a duplicate same-day booking with `status: "active_booking"`, 400).

### Two-active-bookings cap

Both `/whatsapp/web/lookup` and `/whatsapp/web/book` enforce a cap of **2 active
appointments per phone number** (per doctor) when booking for someone else:
- In `/lookup`, if `booking_for_self=False` and a name is given,
  `booking_repository.list_active_appointments_by_phone_number(phone_number, admin_id, doctor_id, 10)`
  is checked — if it returns ≥2 active appointments *and none of them match the submitted
  name* (`matched_same_identity` empty), the request is rejected with
  `_max_active_bookings_message(lang)` (400). If some do match the name, only those matching
  rows are returned (so the widget can re-select the existing person's appointment instead
  of listing everyone else's).
- In `/book`, the same `list_active_appointments_by_phone_number` call is made
  unconditionally before booking; if the count is already ≥2, booking is rejected with the
  same max-active-bookings message (400) regardless of self/someone-else.

`_max_active_bookings_message` delegates to `get_message(lang, "max_active_bookings_reached")`
from `src.messages.templates` — a localized string shared with the conversational FSM's
own booking-cap messaging elsewhere in the codebase.

### Booking, cancel, reschedule — direct `booking_repository` calls

- **`POST /whatsapp/web/book`** — after the same-day-identity check, self-name-mismatch
  check, and 2-booking cap check, it builds a `SimpleNamespace` "context" object mimicking
  whatever shape `booking_repository.save_confirmed_appointment` expects from the
  conversational flow (`patient_name`, `phone_number`, `clinic_id`, `appointment_date`,
  `appointment_time`, `reason="WhatsApp Web Booking"`, `appointment_mode="whatsapp-web"`,
  `booking_channel="whatsapp_web"`, `booking_for_self`, `chat_user_id=None`, `age=None`,
  `gender=None`, `patient_type="existing"`), then calls
  `booking_repository.save_confirmed_appointment(context, admin_id, doctor_id)` directly —
  **no FSM state machine, no turn queue, no session** is involved; this is a straight
  synchronous repository call executed in a thread pool (`run_in_threadpool`, since the
  repository is presumably synchronous/blocking DB code). On success it returns
  `{"status": "booked", "appointment_id", "booking_number"}`; on failure it distinguishes
  "you already have an appointment" (`result.appointment_id` present → `active_booking`,
  or a fallback lookup via `find_active_appointment_by_phone_number`) from a generic error.
- **`POST /whatsapp/web/cancel`** — calls `booking_repository.cancel_appointment(appointment_id, admin_id, "PATIENT")`
  directly (the `"PATIENT"` literal marks who initiated the cancellation, for audit).
- **`POST /whatsapp/web/reschedule`** — resolves the actual chosen time via
  `_resolve_reschedule_time_choice` (handles both an exact `HH:MM` submission and an
  `hour:HH` bucket token from the grouped-time UI, mapping it back to a concrete available
  slot), re-checks for a scheduling conflict via `_reschedule_conflict_exists`, then calls
  `booking_repository.reschedule_appointment_same_clinic(appointment_id, slot_date, slot_time, clinic_id, admin_id, "PATIENT")`
  directly.

All of the DB-touching helper functions in this module (`_resolve_admin_id`, `_doctor_name`,
`_resolve_doctor_id_by_slug`, `_reschedule_conflict_exists`, `_find_self_name_by_phone_number`,
`_find_same_day_identity_appointment`) are wrapped in `run_in_threadpool(...)` when called
from the async route handlers, since the repository layer here uses synchronous DB
connections (`booking_repository._connect()`).

---

## `evolution_webhook_routes.py`

Handles inbound webhooks from an **Evolution API** instance — an unofficial/self-hosted
WhatsApp Web bridge (distinct from Twilio/Meta/Infobip's official Business APIs). Its sole
job is a lightweight **auto-response policy**, not conversational booking: it nudges users
toward the `whatsapp_web_routes.py` self-service widget rather than talking to them.
**It never invokes the FSM or `turn_processor`.**

Registered via
`register_evolution_webhook_routes(app, *, settings, logger, evolution_repository, evolution_policy, evolution_api_client)`.

### Route table

| Method | Path | Purpose |
|---|---|---|
| POST | `{webhook_path}` (default `/evolution/webhook`, or `settings.evolution_webhook_url`) | Evolution API webhook, generic event |
| POST | `{webhook_path}/{event_suffix:path}` | Same, with the event name embedded in the URL path |

Evolution API instances can be configured to POST to a path suffixed with the event type
(e.g. `/evolution/webhook/messages-upsert`); `evolution_webhook_by_event` captures that
suffix and, if the JSON payload itself doesn't already carry an `"event"` field, synthesizes
one from the suffix (`event_suffix.replace("-", ".")`) before handing off to the shared
`_handle(request, event_suffix)`.

### Secret verification

`_verify_secret(request)` — if `settings.evolution_webhook_secret` is configured, it must
match one of `X-Evolution-Webhook-Secret`, `x-evolution-webhook-secret`, or `apikey`
headers (checked in that order, first match wins since they're OR'd into one `presented`
value via chained `or`), else `HTTPException(403)`. If no secret is configured, verification
is skipped entirely (open endpoint) — same "optional but recommended" pattern as Telegram's
legacy secret check.

### Message extraction and event filtering

`_extract_message_event(payload)` normalizes Evolution's WhatsApp-Web-JS-flavored payload
shape:
- Skips non-message events (if `payload["event"]` is present and doesn't contain
  `"message"`, e.g. `connection.update`, it's ignored).
- Reads `data = payload["data"]` (or the payload itself if `data` isn't a dict).
- Skips messages where `data["key"]["fromMe"]` is true (the bot's own outbound messages
  echoed back through the webhook are not "inbound").
- Extracts text from `message.conversation` (plain text messages) or
  `message.extendedTextMessage.text` (replies/quoted messages), falling back to
  `data["text"]`.
- Extracts `remote_jid` (the sender's WhatsApp JID) from `key.remoteJid`/`data.remoteJid`/
  `data.from`/`payload.from`, and `instance_name` from `payload.instance`/`instanceName` or
  the nested `data` equivalents.
- Returns `None` (silently ignored, `{"ok": true, "status": "ignored"}`) if either
  `remote_jid` or non-empty `text` is missing.

### Auto-response policy flow

Once a valid inbound message event is extracted:
1. `evolution_repository.resolve_doctor_context(instance_name=..., connected_account="")`
   maps the Evolution instance to a bound doctor/clinic. If dependencies
   (`evolution_repository`, `evolution_policy`, `evolution_api_client`, or
   `evolution_api_client.enabled`) aren't configured, or the instance has no bound doctor
   (`context` is falsy), the webhook responds `{"ok": true, "status": "disabled"}` or
   `{"ok": true, "status": "unbound"}` respectively and does nothing further — this module
   is explicitly opt-in production infrastructure, silently inert otherwise.
2. `evolution_policy.register_inbound(doctor_id=context.doctor_id, patient_identity=event["remote_jid"])`
   returns an incrementing message `count` for this doctor+patient pair (implemented via
   `EvolutionAutoResponsePolicy`, backed by Redis with a session window —
   `settings.evolution_session_window_seconds` — so the counter resets after a period of
   inactivity, effectively "count of messages in the current session window").
3. **Policy decision by count:**
   - `count == 1` (first message in the window): compose a **welcome message** via
     `_welcome_text(doctor_name, clinic_name, booking_link)`. The booking link points at the
     `whatsapp_web_routes.py` widget: `_build_booking_link(context.slug)` builds
     `{evolution_booking_base_url or QR_BASE_URL}{evolution_booking_path_prefix or /whatsapp/web}/{slug}`.
     The message template is `settings.evolution_welcome_template` if configured, else a
     bilingual (English/Hindi) default embedding the doctor name and booking link.
     `status = "welcome_sent"`.
   - `count == 2` (second message in the window): compose a **warning message** via
     `_warning_text()` — `settings.evolution_warning_text` if configured, else a bilingual
     default asking the user to use the booking link and avoid repeated messages.
     `status = "warning_sent"`.
   - `count >= 3` (or any other value): no reply text is composed at all — `status` stays
     `"silenced"`. This is the "then silence" behavior: after the welcome and one warning,
     the bridge stops auto-replying entirely for the rest of the session window, presumably
     to avoid spamming users who are ignoring the prompts (there is no conversational
     engagement here since the FSM is never invoked).
4. If `reply_text` was composed (count 1 or 2), it's sent via
   `evolution_api_client.send_text(instance_name=event["instance_name"] or context.instance_name, remote_jid=event["remote_jid"], text=reply_text)`
   — a direct call to the Evolution API client's send endpoint, not through
   `ChannelDelivery`/`turn_processor` at all.
5. Every step logs a structured event via `log_event` (`EVOLUTION_WEBHOOK_IN`,
   `EVOLUTION_DOCTOR_LOOKUP`, `EVOLUTION_POLICY_CHECK`, `EVOLUTION_REPLY_COMPOSED`,
   `EVOLUTION_REPLY_SENT`/`EVOLUTION_REPLY_SKIPPED`, `EVOLUTION_WEBHOOK_DONE`) with
   millisecond timings for each phase, keyed by `_chat_log_id(remote_jid)` (derived from the
   JID's user portion before `@`).
6. Returns `{"ok": true, "status": status, "doctor_id": context.doctor_id, "count": count}`.

Because this module never calls `session_manager`, `turn_processor`, or `fsm.handle()`, a
patient messaging the Evolution-bridged number never enters the appointment-booking
conversation — they are only ever redirected to the web widget (`whatsapp_web_routes.py`)
after receiving at most two automated nudges.

---

## Wiring from `main.py`

`main.py` builds every singleton dependency at module import time (LLM client,
repositories, `SessionManager`, Redis client, `RequestValidator`, Twilio `Client`,
`TurnQueueProcessor`/`KafkaTurnBridge`, `UserProcessingGuard`, `UserTurnBuffer`,
`QrCheckinService`, `EvolutionAutoResponsePolicy`, `EvolutionApiClient`, etc.) and then
calls each `register_*_routes` function once, after `app = FastAPI(...)` is constructed.

Notably, `register_webhook_routes` receives most of its dependencies as **zero-arg
lambdas** (`request_validator=lambda: request_validator`, `sid_store=lambda: sid_store`,
`turn_processor=lambda: turn_processor`, `route_cache_client=lambda: _redis_client`, etc.)
rather than the objects themselves. Inside `webhooks.py`, `_resolve(dep)` calls the
lambda if it's callable else returns it as-is, and the module calls `_resolve(...)` fresh
on nearly every request. This indirection exists so that if `main.py` ever reassigns one of
these module-level globals (e.g. `sid_store`, `_redis_client`) after registration, the route
closures pick up the new reference instead of holding a stale one captured at
`register_webhook_routes(...)` call time. It also lets `qr_routes.py` and
`whatsapp_web_routes.py` receive plain object references directly (`booking_repository`,
`scheduling_repository`, `qr_checkin_service` are not reassigned after startup, so no lambda
indirection is needed there).

Five callables are also passed into `register_webhook_routes` for cross-cutting state that
lives in `main.py`: `set_user_bot_identity`/`set_user_route_context` (write into `main.py`'s
`_user_bot_identity`/`_user_route_context` dicts, later read by `_process_turn` to pin FSM
routing and identity), `submit_next_buffered_turn` (drains `_user_turn_buffer` after a turn
completes), and `get_telegram_bot_username` (reads the runtime-resolved bot username
resolved once at startup by `_resolve_telegram_bot_username`).
