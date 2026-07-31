# src/evolution

This package is the bridge between the clinic-appointment-bot and a self-hosted **Evolution API**
WhatsApp gateway (`docker-compose.yml` runs `evoapicloud/evolution-api:v2.3.7`). It is deliberately
narrow: it does **not** drive the conversational FSM, does **not** parse intents, and does **not**
book appointments. Its only job is to notice that a patient messaged an Evolution-connected WhatsApp
number and nudge them, at most twice per session window, toward the web booking widget
(`src/whatsapp_web/`). Everything else the patient does happens outside of this package, in the
browser.

Back to [root README](../../README.md).

## How it fits together

`src/api/evolution_webhook_routes.py` (`register_evolution_webhook_routes`) is the FastAPI entry
point. When Evolution POSTs an inbound-message webhook event, the route handler:

1. Extracts `instance_name`, `remote_jid`, and message `text` from the Evolution payload
   (`_extract_message_event`), ignoring anything that isn't an inbound message (`fromMe` events are
   dropped).
2. Calls `EvolutionRepository.resolve_doctor_context(...)` to map the Evolution `instance_name` to a
   doctor/clinic.
3. Calls `EvolutionAutoResponsePolicy.register_inbound(...)` to get the running message count for
   that patient within the current session window.
4. Based on the count, composes a welcome message (count == 1) or a warning message (count == 2), or
   sends nothing (count >= 3).
5. If there is a reply, sends it via `EvolutionApiClient.send_text(...)`.

None of this touches `src/fsm` or the normal chat pipeline — Evolution-connected numbers get this
auto-responder only. See [src/api/README.md](../api/README.md) for the full webhook route contract
(request/response shapes, secret verification, logging) and
[src/whatsapp_web/README.md](../whatsapp_web/README.md) for what the booking link in the welcome
message actually leads to.

## `api_client.py` — `EvolutionApiClient`

`EvolutionApiClient` is a thin wrapper around Evolution's "send text" REST endpoint, built on the
stdlib `urllib.request` (no external HTTP dependency).

- Constructed with an `EvolutionApiSettings` dataclass: `base_url`, `api_key`,
  `send_text_path_template`.
- `enabled` is `True` only when both `base_url` and `api_key` are non-empty — callers (the webhook
  route) check this before doing any Evolution-related work, so a clinic without Evolution
  configured simply gets `status: "disabled"` on inbound webhooks.
- `send_text(*, instance_name, remote_jid, text)`:
  - Requires a non-empty `instance_name` and a `remote_jid`; raises `RuntimeError` otherwise (or if
    the client isn't `enabled`).
  - Derives the plain phone `number` from `remote_jid` by taking everything before the first `@`
    (Evolution JIDs look like `919876543210@s.whatsapp.net`).
  - Builds the target path by formatting `send_text_path_template` with `instance=instance_name`
    (default template `/message/sendText/{instance}`, so e.g.
    `/message/sendText/my-clinic-instance`), then joins it onto `base_url`.
  - POSTs a JSON body `{"number": <phone>, "text": <text>}` with headers
    `Content-Type: application/json` and `apikey: <EVOLUTION_API_KEY>` — Evolution API's own
    convention is an `apikey` header rather than `Authorization: Bearer`.
  - Uses a 20-second timeout and does not retry; a non-2xx response raises via
    `urlopen` (an `HTTPError`), which propagates up to the caller.

## `policy.py` — `EvolutionAutoResponsePolicy`

This is the session-window rate limiter that decides *whether* to reply and *what tier* of reply to
send. It does not compose message text itself (that's the webhook route's job) — it only returns an
integer count.

- Constructed with `redis_client` (optional), `session_window_seconds` (default `6 * 60 * 60`, i.e. 6
  hours, wired from the `EVOLUTION_SESSION_WINDOW_SECONDS` env var — clamped to a 60-second minimum),
  and `key_prefix` (default `"msgbot"`, shared with the rest of the app's Redis namespace).
- `register_inbound(*, doctor_id, patient_identity) -> int`: called once per inbound Evolution
  message. Returns the number of messages seen from this patient, for this doctor, within the
  current session window.
  - **Redis path** (when `redis_client` is provided): does `INCR` on the key; if this is the first
    increment (count == 1) it also sets `EXPIRE` to `session_window_seconds`. This gives a proper
    sliding/expiring session window backed by Redis TTL. Any Redis exception is swallowed and falls
    through to the in-memory path, so a Redis outage degrades gracefully rather than breaking the
    webhook.
  - **In-memory fallback**: `_register_inbound_local` keeps a `dict[key -> (window_start_ts, count)]`
    guarded by a `threading.Lock`. If the window has expired (`now - window_start >=
    session_window_seconds`), the counter resets to 1; otherwise it increments. `_prune_local` sweeps
    expired entries on every call to bound memory growth. Note this fallback is per-process — it will
    not stay consistent across multiple worker processes/instances, which is one reason Redis is the
    intended production backend.
- **Key pattern**: `EvolutionAutoResponsePolicy._key` builds
  `f"{key_prefix}:evolution:auto:{doctor_id}:{normalized_patient_identity}"`, e.g.
  `msgbot:evolution:auto:42:919876543210`.
- **Identity normalization** (`_normalize_patient_identity`): lowercases the input, strips a
  `whatsapp:` prefix if present, strips anything after `@` (so a full JID like
  `919876543210@s.whatsapp.net` becomes `919876543210`), then keeps only digits. Falls back to the
  cleaned string, or `"unknown"`, if no digits are found. This ensures the same patient is recognized
  regardless of whether the identity arrives as a raw phone number, a `whatsapp:+91...` string, or a
  full Evolution JID.

### The three-tier behavior

The webhook route (`src/api/evolution_webhook_routes.py`) interprets the count returned by
`register_inbound` as:

| count | behavior |
|---|---|
| 1 (first message in window) | send the welcome message, containing the web booking link |
| 2 (second message in window) | send a one-time warning telling the patient to use the link instead of messaging again |
| 3+ (later messages in window) | stay silent — no reply is sent |

Once the window expires (default 6 hours since the first message), the count resets and the patient
gets the welcome message again on their next inbound message.

## `__init__.py`

Re-exports `EvolutionApiClient`, `EvolutionApiSettings`, and `EvolutionAutoResponsePolicy` for
convenient importing (`from src.evolution import ...`), as used in `main.py`.

## `src/repositories/evolution_repository.py` — `EvolutionRepository`

Not physically inside `src/evolution/`, but it is the third leg this package depends on, so it's
documented here.

- `ensure_schema()` creates the `doctor_evolution_bindings` table if it doesn't exist:
  `binding_id`, `doctor_id`, `clinic_id`, `evolution_instance_name` (unique),
  `evolution_account_identity` (unique), `status` (default `'ACTIVE'`), timestamps. This is the table
  that links an Evolution instance to a doctor/clinic in this app's MySQL database.
- `resolve_doctor_context(*, instance_name="", connected_account="") -> Optional[EvolutionDoctorContext]`:
  - First tries `_resolve_by_instance(instance_name)`: joins `doctor_evolution_bindings` (status
    `ACTIVE`, matching `evolution_instance_name`) to `doctors` and `clinics`. If the binding has no
    `clinic_id` / clinic name, it falls back to `_first_clinic_for_doctor` (lowest `clinic_id` with
    `status = 'ACTIVE' OR status IS NULL`).
  - If that fails and `connected_account` was given, falls back to `_resolve_by_connected_account`:
    normalizes the phone number and scans active `doctors` rows for a matching (digits-only) `phone`.
  - Returns an `EvolutionDoctorContext` (frozen dataclass): `doctor_id`, `admin_id`, `clinic_id`,
    `instance_name`, `account_identity`, `doctor_name`, `clinic_name`, `slug`. `slug` is what
    `_build_booking_link` in the webhook route uses to build the patient-facing booking URL
    (`{EVOLUTION_BOOKING_BASE_URL}{EVOLUTION_BOOKING_PATH_PREFIX}/{slug}`, e.g.
    `.../whatsapp/web/dr-sharma-clinic`).
  - Phone/identity normalization (`_normalize_identity`) strips a `whatsapp:` prefix, strips anything
    after `@`, and prefers digits-only — mirroring the normalization in `policy.py`.
- If neither instance nor connected-account resolves to a binding, the webhook route treats the
  message as `unbound` and does nothing (no reply is sent, since there's no doctor context to build
  one from).

## Environment variables

Loaded in `src/config.py` (`load_settings`), sourced from the process environment or
`.env.example` defaults:

| Variable | Purpose | Default |
|---|---|---|
| `EVOLUTION_API_BASE_URL` (or `EVOLUTION_API_MANAGER_URL`) | Base URL of the self-hosted Evolution API instance (e.g. `http://localhost:8080`) | `""` |
| `EVOLUTION_API_KEY` (or `AUTHENTICATION_API_KEY`) | API key sent as the `apikey` header on every Evolution request | `""` |
| `EVOLUTION_WEBHOOK_URL` | The webhook path Evolution should call; also used to derive the route path registered in FastAPI | `""` (falls back to `/evolution/webhook`) |
| `EVOLUTION_WEBHOOK_SECRET` | If set, inbound webhook requests must present this value in `X-Evolution-Webhook-Secret` / `x-evolution-webhook-secret` / `apikey`, or the route returns 403 | `""` (no verification) |
| `EVOLUTION_SEND_TEXT_PATH_TEMPLATE` | Path template (with `{instance}` placeholder) used to build the "send text" URL | `/message/sendText/{instance}` |
| `EVOLUTION_BOOKING_BASE_URL` (or `DOCTER_EVOLUTION_API_BASE_URL`, or `QR_BASE_URL` as final fallback) | Public base URL used to build the booking link sent in the welcome message | `""` |
| `EVOLUTION_BOOKING_PATH_PREFIX` | Path prefix under the booking base URL, appended with the doctor's `slug` | `/whatsapp/web` |
| `EVOLUTION_SESSION_WINDOW_SECONDS` | Session window for the auto-response policy (seconds) | `21600` (6 hours) |
| `EVOLUTION_WELCOME_TEMPLATE` | Overrides the default bilingual welcome message template (supports `{doctor_name}`, `{clinic_name}`, `{booking_link}`) | built-in EN/HI template |
| `EVOLUTION_WARNING_TEXT` | Overrides the default bilingual second-message warning text | built-in EN/HI text |

## Wiring in `main.py`

`main.py` constructs the three collaborators and passes them into
`register_evolution_webhook_routes`:

```python
evolution_repository = EvolutionRepository(booking_repository._config)
evolution_policy = EvolutionAutoResponsePolicy(
    redis_client=_redis_client,
    session_window_seconds=settings.evolution_session_window_seconds,
    key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot"),
)
evolution_api_client = EvolutionApiClient(
    EvolutionApiSettings(
        base_url=settings.evolution_api_base_url,
        api_key=settings.evolution_api_key,
        send_text_path_template=settings.evolution_send_text_path_template,
    )
)
```

`_redis_client` comes from `build_redis_client_from_env()` (`src/runtime/user_processing_guard.py`),
the same Redis connection used elsewhere in the app (session store, processing guard, etc.), so the
Evolution session-window counters share Redis with the rest of the runtime when Redis is configured,
and fall back to per-process memory when it isn't.
