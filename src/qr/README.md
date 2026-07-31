# QR Check-in Subsystem

This package implements walk-in / front-desk patient check-in over plain HTTP and QR
codes, entirely separate from the WhatsApp/conversational booking FSM. It has three
independent flows — per-doctor QR check-in, hospital-wide QR check-in, and hospital
"registration" (token-only, no appointment) — plus the supporting QR image generator
(`generator_service.py`) and bilingual static page renderer (`page_renderer.py`). All
three flows funnel through `QrCheckinService`, which talks directly to
`booking_repository` and `scheduling_repository` (raw SQL against `patients`,
`doctors`, `clinics`, `doctor_clinic_schedule`, the appointment table, and a
QR-specific `qr_walkin_queue` table) instead of going through the chat session
state machine.

See [src/api/README.md](../api/README.md) for the HTTP route surface (`src/api/qr_routes.py`)
that calls into this module — this document covers the service-layer contract only.

Back to [root README](../../README.md).

## Module layout

- `checkin_service.py` — `QrCheckinService`, the only stateful/DB-aware class in this
  package. Owns all three flows plus Redis caching and `ensure_schema()`.
- `generator_service.py` — pure functions that build the QR-encoded URLs, render an
  SVG QR code, and produce filenames/data URLs for download and preview.
- `page_renderer.py` — pure functions that return complete static HTML strings (no
  template engine, no frontend framework) for the check-in and registration pages.
- `__init__.py` — re-exports `QrCheckinService` as `src.qr.QrCheckinService`.

`main.py` wires these together: it constructs `QrCheckinService(booking_repository=...,
scheduling_repository=...)` only when both repositories are configured, calls
`set_redis_client(...)` / `set_cache_config(...)` on startup, calls
`qr_checkin_service.ensure_schema()` during app startup, and passes the instance into
`register_qr_routes(app, qr_checkin_service=..., ...)` which mounts all `/qr/*` FastAPI
routes in `src/api/qr_routes.py`.

## Flow 1: Per-doctor / per-clinic QR check-in

**Generation.** A clinic admin calls `POST /qr/generate?doctor_id=&clinic_id=`. The
route builds a booking URL with `generator_service.build_qr_booking_url(base_url=...,
doctor_id=..., clinic_id=...)`, which encodes to:

```
{base_url}/qr/checkin?doctor_id={doctor_id}&clinic_id={clinic_id}
```

That URL is rendered to an SVG with `build_qr_svg` and returned as a base64 data URL
(`svg_to_data_url`) for preview, plus a `download_path` that streams the raw SVG with
`build_download_filename` as the attachment name. The clinic prints this QR code and
posts it at the doctor's desk.

**Scan.** `GET /qr/checkin?doctor_id=&clinic_id=` resolves display names via
`QrCheckinService.resolve_doctor_and_clinic(doctor_id, clinic_id)` (simple `SELECT` on
`doctors` / `clinics`, defaulting to "Doctor"/"Clinic" if not found) and renders
`page_renderer.render_qr_page_html(...)` — a static form asking for `patient_name` and
`phone_number`.

**Submit.** The page's JS `fetch`es `POST /qr/checkin/submit` with
`{doctor_id, clinic_id, patient_name, phone_number, detected_language}`. The route
calls `QrCheckinService.process_checkin(doctor_id=, clinic_id=, patient_name=, phone=,
language=)` in a threadpool and returns a `QrCheckinResult` as JSON (or as a rendered
HTML page if the request's `Accept` header prefers `text/html`).

### `process_checkin` logic (read from `checkin_service.py`, `QrCheckinService.process_checkin`)

This method bypasses the WhatsApp conversational FSM entirely — it never touches
`SessionManager` or any chat state; it is a self-contained transactional booking path:

1. **Validate input.** Normalizes the phone (`_normalize_phone` strips `whatsapp:`
   prefix and non-digits) and trims the name. Rejects empty name or phone length
   outside `[10, 15]` digits with `status="error"`.
2. **Resolve `admin_id`.** `_resolve_admin_id(doctor_id)` looks up `doctors.admin_id`
   for the given doctor, falling back to `booking_repository.default_admin_id()`. If
   no admin can be resolved, returns `status="error"` ("Doctor/admin mapping is not
   configured").
3. **Resolve display names** via `resolve_doctor_and_clinic`.
4. **Already has an active booking.** `_active_booking(phone, admin_id, doctor_id,
   clinic_id)` queries today's appointment for that phone/doctor/clinic combo with
   status in `('BOOKED', 'PENDING', 'CONFIRMED')`. If found, `process_checkin` returns
   immediately with `status="active_booking"`, echoing the existing `booking_number`,
   `appointment_date`/`appointment_time` (formatted 12-hour via `_format_time_12h`).
   No new row is written — this is a dedupe guard against re-scanning the same QR twice.
5. **Try a first available regular slot.** `_first_available_slot` asks
   `scheduling_repository.list_available_times(doctor_id=, clinic_id=,
   slot_date=today, admin_id=, limit=60)` for today's next open time. In parallel it
   computes `_select_qr_overflow_session` (today's schedule row that is currently in
   progress, or one that ended within the last `qr_overflow_extension_minutes`) and
   `_should_prefer_regular_slot` (true only if the first available regular slot's
   datetime is between "now" and "now + overflow extension"). If a regular slot exists
   and should be preferred (or there is no overflow session to fall back to at all),
   it books it as a normal appointment via `booking_repository.save_confirmed_appointment(
   context=..., admin_id=, doctor_id=)` with a synthetic context
   (`booking_channel="qr_scan"`, `appointment_mode="walk-in"`, `reason="QR Walk-in"`).
   This is the same repository call the FSM's own confirmation step would use, so a
   direct-slot QR booking looks identical in the DB to a chat-booked one, just tagged
   `channel="qr_scan"` where that column exists.
6. **Overflow booking.** If there is no usable regular slot (`should_use_regular_slot`
   is false and an overflow session was found), `process_checkin` calls
   `_book_confirmed_overflow(admin_id=, doctor_id=, clinic_id=, patient_name=, phone=,
   target_session=qr_overflow_session)`. This method:
   - Opens a DB transaction and requires appointment-mode schema
     (`_use_appointment_mode()`); raises if the schema is slot-mode only.
   - Loads today's `doctor_clinic_schedule` rows, normalizes them, and picks the
     target session (current session if the clinic is mid-session "now", otherwise the
     next/most recent session per `_select_overflow_session`).
   - Computes `total_regular_slots` for that session (session duration ÷
     `slot_duration`) and the set of "regular" slot start times
     (`_regular_slot_starts_for_day`).
   - Upserts the `patients` row by phone (and updates `doctor_id`/name).
   - Counts existing overflow appointments after the session's regular end time
     (`channel = 'qr_scan'` where that column exists) to compute an `overflow_index`,
     and an `overflow_booking_id = total_regular_slots + overflow_index` — i.e.
     overflow patients get booking numbers that continue past the clinic's normal
     numbered slots (slot 1..N are regular, N+1.. are overflow arrivals).
   - Rounds "now" up to the next slot-duration boundary (`_round_up_to_slot_boundary`)
     and walks forward in `slot_duration`-minute increments looking for a free
     `start_time` in the appointment table for that doctor/date, inserting a new
     `BOOKED` appointment (or reviving a `CANCELLED` row at that time) once a free slot
     is found. If the found start time happens to coincide with a regular slot
     boundary, the booking is numbered with the regular slot number instead of the
     overflow counter (`_regular_slot_number_for_start`).
   - Writes the resulting `booking_id` back onto `patients.booking_id` (if that column
     exists), commits, and best-effort logs an SMS confirmation notification via
     `booking_repository.log_notification_event(event_type="CONFIRMATION", channel="sms",
     status="PENDING", ...)` — failures here are logged but never fail the booking.
   - Returns `(appointment_id, assigned_booking_id, date, formatted_time)`.

   If `_book_confirmed_overflow` raises (e.g. "Doctor schedule is not configured for
   this clinic."), `process_checkin` catches it and returns `status="error"` with a
   translated message.

Both success paths return `status="booked"` with a message built from
`get_qr_message(language, "qr_confirmed_token", token_id=...)` plus an
`"qr_estimated_time"` line when a time is known. All user-facing strings are looked up
through `src.messages.templates.get_qr_message` (for `QrCheckinResult.message`) and,
for the standalone hospital-registration path, through the service's own private
`_msg()` template table (English/Hindi/Hinglish).

**`QR_OVERFLOW_EXTENSION_MINUTES`** (env var, default `90`, floored at `0`, read once
in `QrCheckinService.__init__` into `self.qr_overflow_extension_minutes`) controls how
long after a session's official end time (or how far before its start) a scan is still
considered "in/near session" for overflow purposes — it governs both
`_select_qr_overflow_session` (which session to attach overflow walk-ins to) and
`_should_prefer_regular_slot` / `resolve_hospital_qr_schedule`'s session ranking.

There is currently no separate "add to overflow queue" (pure wait-list, no appointment
row) path reachable from `process_checkin` itself — overflow patients are always given
a confirmed appointment row via `_book_confirmed_overflow`. The `qr_walkin_queue` table
and `_enqueue_overflow` helper (queue position + estimated time, unique per
doctor/clinic/date/phone/status) exist in the service for a pure-waitlist queueing model
but are not currently invoked by `process_checkin`; `QrCheckinResult` still carries
`queue_position` / `estimated_time` fields for when that path is wired in.

## Flow 2: Hospital-wide QR check-in

Unlike the per-doctor flow, a hospital prints **one** QR code covering every clinic in
its group; the patient picks a specialization and doctor on the page itself.

**Generation.** `POST /qr/hospital/generate` builds the URL with
`generator_service.build_qr_hospital_url(base_url=, hospital_code=)`:

```
{base_url}/qr/hospital/checkin?hospital_code={hospital_code}
```

encoded to SVG the same way as flow 1, with `build_hospital_download_filename` for the
attachment name.

**Scan.** `GET /qr/hospital/checkin?hospital_code=` calls
`QrCheckinService.hospital_qr_options(hospital_code)` and renders
`page_renderer.render_hospital_qr_page_html(hospital_code=, options=, ...)`, a page with
specialization/doctor `<select>` dropdowns populated client-side from the JSON in
`options["doctors"]` / `options["specializations"]`.

### `hospital_qr_options` and Redis caching

`hospital_qr_options(hospital_code)` is a cache-aside read:

1. Look up `self._hospital_options_cache_key(code)` =
   `f"{key_prefix}:hospital:opts:{code}"` in Redis via `_load_cached_hospital_options`.
   On a hit (valid JSON dict), return immediately — no DB hit.
2. On a miss (or no Redis client configured), fall back to direct DB reads:
   - `list_hospital_qr_doctors(hospital_code)` — joins `clinics` → `doctor_clinic_schedule`
     → `doctors` filtered to the hospital's `hospital_group_code` column (detected via
     `_hospital_group_column()` / `_column_exists`), active status, active date ranges,
     and *today's* day-of-week schedule row, returning distinct
     `(doctor_id, doctor_name, specialization)` tuples as `HospitalQrDoctorOption`.
   - `hospital_qr_display_name(hospital_code)` — picks the most common non-empty
     `clinic_name` among that hospital's active clinics as the display name.
   - Assembles `{"hospital_code", "hospital_name", "specializations" (sorted, deduped),
     "doctors" (list of dicts)}`.
3. Writes the result back to Redis with `_save_cached_hospital_options`, using
   `SET ... EX {ttl}` where `ttl` is `self._hospital_cache_ttl_seconds`.

The cache key namespace is separate from the general app cache
(`msgbot:hospital:opts:{code}` vs. e.g. `msgbot:doctor:...`), and any Redis errors on
either read or write are swallowed (`except Exception: return`/`None`) so the DB
fallback always works even if Redis is down.

`GET /qr/hospital/options?hospital_code=` exposes the same method directly as a JSON
API (used e.g. to refresh the dropdown without a full page reload).

### `resolve_hospital_qr_schedule` and submit

**Submit.** `POST /qr/hospital/checkin/submit` receives
`{hospital_code, doctor_id, patient_name, phone_number, detected_language, ...}`. The
route first calls `QrCheckinService.resolve_hospital_qr_schedule(hospital_code=,
doctor_id=)` to pick *which clinic* the patient's chosen doctor is actually holding
today, since the hospital QR does not encode a `clinic_id`:

- Queries `clinics` ⋈ `doctor_clinic_schedule` ⋈ `doctors` for the given hospital group
  and doctor, restricted to active status, active date ranges, and today's
  day-of-week, returning every matching schedule row (a doctor can have multiple
  clinic sessions today).
- If no rows match, returns `None` and the route responds with `status="error"` /
  "No valid doctor schedule is available for this hospital right now." (HTTP 400).
- Otherwise ranks the candidate sessions with an internal `rank()` function using the
  same overflow-extension window as flow 1: rank `0` = session currently in progress,
  `1` = session ended within `qr_overflow_extension_minutes`, `2` = session starts
  later today, `3` = anything else (fallback), `4` = unparsable times. The
  lowest-ranked (soonest-relevant) row wins and is returned as a
  `HospitalQrScheduleResolution(doctor_id, doctor_name, clinic_id, clinic_name,
  specialization)`.
- The route then calls the **same** `QrCheckinService.process_checkin(doctor_id=
  schedule.doctor_id, clinic_id=schedule.clinic_id, patient_name=, phone=, language=)`
  used by flow 1 — hospital-wide check-in is really flow 1 with the clinic resolved
  dynamically instead of being baked into the QR code. All overflow/active-booking/
  confirmed-slot behavior described above applies identically.

## Flow 3: Hospital "registration" (no appointment)

This is deliberately isolated from flows 1 and 2 — the code comment in
`page_renderer.render_hospital_registration_page_html` calls it "Standalone hospital
REGISTRATION page... on submit it shows a generated unique token instead of booking an
appointment," and `qr_routes.py` labels the whole block "Hospital REGISTRATION flow
(static, NO DB, isolated from all above)" for the page/generate handlers — only the
submit handler touches the DB, and only to upsert a `patients` row, never an
appointment.

**Generation.** `POST /qr/hospital/registration/generate` builds the URL with
`generator_service.build_qr_hospital_registration_url(base_url=, hospital_code=,
hospital_name=)`:

```
{base_url}/qr/hospital/registration/checkin?hospital_code={code}&hospital_name={name}
```

(`hospital_name` is only included if non-empty; it seeds a fallback display name.)

**Scan.** `GET /qr/hospital/registration/checkin` reuses
`QrCheckinService.hospital_qr_options(hospital_code)` to populate the same
doctor/specialization dropdowns as flow 2 (doctor is optional here), and renders
`page_renderer.render_hospital_registration_page_html(hospital_code=, hospital_name=,
doctors=, specializations=, language=)`. The form additionally collects `age` and
`gender` (not required by flows 1/2).

**Submit.** `POST /qr/hospital/registration/submit` requires
`patient_name`, `age`, `gender`, `phone_number` (400 if any missing); `doctor_id` is
optional (`0` → no doctor, stored as `NULL`). It calls
`QrCheckinService.register_hospital_patient(hospital_code=, doctor_id=, patient_name=,
phone=, age=, gender=)`.

### `register_hospital_patient` logic

Read directly from `checkin_service.py`:

1. Validates name and normalizes/validates phone the same way as `process_checkin`.
2. Resolves `admin_id`: prefers the chosen doctor's `admin_id`
   (`_resolve_admin_id(doctor_id)`) when a doctor was selected, otherwise falls back to
   `_resolve_hospital_admin_id(hospital_code)` (first active clinic's `admin_id` in the
   hospital group) so registration works even with no doctor chosen. Errors out if
   neither resolves (hospital not configured for registration).
3. Builds today's token prefix: `code = re.sub(r"[^A-Za-z0-9]", "", hospital_code).upper()
   or "REG"`, then `today_prefix = f"{code}/{YYYY}/{MM}/{DD}/"`.
4. Looks up an existing `patients` row for this `admin_id` + normalized name + phone
   (`FOR UPDATE`, matching either full or last-10-digit phone, restricted to
   `profile_type = 'SELF'` where that column exists).
5. **Daily uniqueness / already-registered check.** If a matching patient row exists
   and its `tmpregtoken` already starts with today's prefix, the method rolls back and
   returns `{"status": "already_registered", "token": <existing token>, "message": ...}`
   — no new row, no new sequence number is consumed.
6. Otherwise it mints a new token via `next_registration_sequence(hospital_code)` and
   formats `token = f"{today_prefix}{sequence:05d}"` (5-digit zero-padded daily
   sequence, e.g. `NAH/2026/07/31/00007`).
7. Upserts `patients`: `UPDATE` if the row was found (refreshing name/phone, and
   conditionally `doctor_id`, `hospital_group_code`, `age`, `gender`, `tmpregtoken` when
   those columns exist and values were supplied), otherwise `INSERT` a new row with
   `profile_type = 'SELF'` where supported. Commits and returns
   `{"status": "registered", "token": ..., "message": "Registration successful. Your
   token is {token}."}`.

### Daily-unique token via `next_registration_sequence`

`next_registration_sequence(hospital_code)` is the atomic counter backing the token's
5-digit sequence:

- Normalizes `hospital_code` to lowercase (`code`), computes `day =
  now_in_runtime_timezone().strftime("%Y%m%d")`.
- Redis key pattern: `f"{key_prefix}:registration:seq:{code}:{day}"` — e.g.
  `msgbot:registration:seq:nah:20260731`.
- Uses `INCR` on that key for an atomic per-hospital-per-day counter. On the very first
  increment of the day (`seq == 1`) it sets a `172800`-second (~2 day) TTL so the key
  self-expires and the next day starts fresh at `1` again without any cron/cleanup job.
- If Redis is unavailable (no client, or the call raises), falls back to a
  non-sequential but still-unique-within-the-day number:
  `int(now_in_runtime_timezone().strftime("%H%M%S%f"))` — the DB upsert still
  succeeds, it just won't be a clean incrementing sequence in that degraded mode.

**Purpose.** This token is meant for **front-desk pre-registration** — a patient scans
the hospital's registration QR while waiting, fills in their basic details, and gets a
token/queue number they can show at the front desk (the registration page can also
render the token to a downloadable PNG client-side via `downloadTokenImage` in the
generated HTML, entirely in-browser with `<canvas>`, no server round-trip). It does
**not** create an appointment, slot, or `qr_walkin_queue` entry — it exists purely to
hand the patient a stable, human-presentable reference number while the front desk
handles the actual scheduling separately.

## QR image generation (`generator_service.py`)

Depends on the third-party `qrcode` package (`import qrcode`, using
`qrcode.image.svg.SvgPathImage` as the image factory) — this is the only external
dependency in this module beyond the standard library.

- **URL builders** — `build_qr_booking_url`, `build_qr_hospital_url`,
  `build_qr_hospital_registration_url` — each takes a `base_url` plus the relevant IDs
  and returns the fully query-encoded check-in URL that gets embedded in the QR code
  (see the per-flow sections above for the exact URL shapes).
- **`build_qr_svg(*, url)`** — creates a `qrcode.QRCode` with `error_correction=
  ERROR_CORRECT_M`, `box_size=10`, `border=4`, encodes `url`, and renders it through
  `SvgPathImage`. Because `SvgPathImage` only draws the black modules on a
  **transparent** background (invisible on dark UIs and often unscannable), the
  function post-processes the generated SVG with a regex that injects
  `<rect width="100%" height="100%" fill="#ffffff"/>` immediately after the opening
  `<svg>` tag, guaranteeing a solid white backdrop including the quiet zone.
- **`svg_to_data_url(svg_markup)`** — base64-encodes the SVG string and wraps it as
  `data:image/svg+xml;base64,{...}` for inline `<img src=...>` / JSON preview use
  (`preview_data_url` in the `/qr/generate` and `/qr/hospital/generate` responses).
- **Filename builders** — `build_download_filename(doctor_name=, clinic_name=,
  doctor_id=, clinic_id=)` and `build_hospital_download_filename(hospital_name=,
  hospital_code=)` slugify the human-readable names (lowercase, non-alphanumeric runs
  collapsed to `-`) and produce `qr-{slug}.svg` / `qr-hospital-{slug}.svg`, with a
  numeric-ID fallback if slugification produces an empty string. These names are used
  as the `Content-Disposition: attachment; filename=...` on the `/qr/generate/download`,
  `/qr/hospital/generate/download`, and registration download routes, and echoed back
  as `filename` in the JSON preview responses.

This module is used identically by both the per-doctor generator route
(`/qr/generate*`) and the hospital-wide generator route (`/qr/hospital/generate*`) —
only the URL builder and filename builder differ; `build_qr_svg`/`svg_to_data_url` are
shared verbatim.

## Page rendering (`page_renderer.py`)

All three flows are rendered as **self-contained static HTML strings** — there is no
Jinja/Django-style template engine and no external frontend framework (no React/Vue,
no bundler); each `render_*` function returns a single f-string containing inline
`<style>` and `<script>` blocks. Client-side interactivity (form submission via
`fetch`, dropdown population, the confirmation modal, and — for registration — an
in-browser `<canvas>` token image download) is hand-written vanilla JS embedded in that
string. A shared visual language (colors, card/modal styling) is duplicated across
`render_qr_page_html`, `render_hospital_registration_page_html`, and
`render_hospital_qr_page_html`. The clinic/hospital logo is inlined too:
`_dapto_logo_src()` reads `Dapto_logo.jpeg` from the repo root (two directories above
this file) once (`@lru_cache(maxsize=1)`) and embeds it as a base64
`data:image/jpeg;base64,...` `<img>` source, falling back to an empty string (image
hidden via `onerror`) if the file can't be read.

Each renderer supports three languages — `en`, `hi`, `hinglish` — selected by the
`language` parameter and defaulted to `en` for any other value
(`lang = language if language in {"en", "hi", "hinglish"} else "en"`). Per-flow UI copy
(labels, buttons, modal titles, error strings) is embedded server-side into the initial
HTML **and** duplicated into a client-side JS translation table (`t` / `labels`) so the
page can re-render text without a reload if the client toggles language after load.
Database-sourced values (hospital name, doctor name, specialization, clinic name) are
never translated — they're rendered exactly as stored.

### Language resolution order

Resolution happens once per request in `src/api/qr_routes.py`'s
`_resolve_effective_language(request, payload)`, in this priority order:

1. **Query parameter** `?lang=` on the request URL (`en`, `hi`, or `hinglish`) — if
   present and valid, wins outright and also sets `lock_language = True`.
2. **`detected_language`** field in the submitted JSON/form `payload` (only accepted
   as `en` or `hi` — `_normalize_lang(..., allow_hinglish=False)` — Hinglish is not
   recognized at this stage from the payload).
3. **`Accept-Language` request header** — `_lang_from_accept_header` parses the
   comma-separated header, taking the first token that normalizes to `en` or `hi`.
4. **Default** — `"en"` if none of the above match, with `lock_language = False`.

### "Language lock" concept

`lock_language` is `True` only when the language came from an explicit `?lang=` query
parameter (steps 2–4 all leave it `False`). It is threaded through to
`render_qr_page_html(..., lock_language=lock_language)` and
`render_hospital_qr_page_html(..., lock_language=lock_language)`, where it is emitted
into the page as a JS constant: `const lockLanguage = {"true"/"false"}`. The intent is
that a caller who pins the language via the URL (e.g. a hospital that always wants its
QR pointing at a Hindi-only page) should have that choice respected rather than
overridden by client-side language-detection/switching logic; as written today,
`lockLanguage` is exposed to the page's JS but the script does not yet branch on it
anywhere else — there is no visible in-page language switcher to gate. Treat it as a
reserved hook for that behavior rather than an enforced restriction.

## Redis caching configuration

`QrCheckinService` owns its own Redis wiring, separate from other repositories'
caches:

- **`set_redis_client(redis_client, *, key_prefix=None)`** — called from `main.py`
  once at startup with the shared app Redis client and
  `key_prefix=os.getenv("REDIS_KEY_PREFIX", "msgbot")`. If never called (or called with
  `None`), every cache read/write becomes a no-op and the service transparently falls
  back to direct DB reads (`hospital_qr_options`) or a timestamp-based token fallback
  (`next_registration_sequence`).
- **`set_cache_config(*, ttl_seconds=None, key_prefix=None)`** — called from `main.py`
  with `ttl_seconds=int(os.getenv("REDIS_HOSPITAL_CACHE_TTL_SECONDS", "300"))`. The
  constructor also reads this env var directly as its own default (also floored at a
  minimum of `30` seconds either way), so `REDIS_HOSPITAL_CACHE_TTL_SECONDS` controls
  how long `hospital_qr_options` results are cached (default 5 minutes / 300s).
- **Key prefix** — `self._cache_key_prefix`, default `"msgbot"`, overridable via
  `REDIS_KEY_PREFIX`. All keys this module writes are namespaced under it:
  - `{prefix}:hospital:opts:{hospital_code}` — cached `hospital_qr_options` payload
    (flow 2), TTL = `REDIS_HOSPITAL_CACHE_TTL_SECONDS`.
  - `{prefix}:registration:seq:{hospital_code_lower}:{YYYYMMDD}` — daily registration
    token counter (flow 3), TTL fixed at 172800s (~2 days) regardless of the hospital
    cache TTL setting.
- **`QR_OVERFLOW_EXTENSION_MINUTES`** (default `90`) is a plain env var read directly
  in the constructor — it is not Redis-backed, but it's the other key tunable for this
  module's behavior (see Flow 1's overflow section above).

All Redis operations in this module are wrapped in broad `try/except: return None` (or
equivalent) guards, so a Redis outage degrades the subsystem (slower hospital-options
lookups hitting the DB every time; less-clean registration token numbers) but never
makes it unavailable.

## Schema: `ensure_schema()`

`QrCheckinService.ensure_schema()` is called once during application startup from
`main.py` (guarded by `if qr_checkin_service: ... except Exception: LOGGER.warning(...)`
so a schema failure doesn't crash boot). It is intentionally minimal — it does **not**
manage the `patients`, `doctors`, `clinics`, or appointment tables (those are owned by
`booking_repository`/`scheduling_repository` elsewhere); it only creates the QR-specific
overflow queue table if missing:

```sql
CREATE TABLE IF NOT EXISTS qr_walkin_queue (
    queue_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    doctor_id BIGINT NOT NULL,
    clinic_id BIGINT NOT NULL,
    patient_name VARCHAR(255) NOT NULL,
    phone VARCHAR(32) NOT NULL,
    queue_date DATE NOT NULL,
    queue_position INT NOT NULL,
    estimated_time DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'WAITING',
    source_channel VARCHAR(20) NOT NULL DEFAULT 'qr',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_qr_waiting_lookup (doctor_id, clinic_id, queue_date, status, queue_position),
    UNIQUE KEY uq_qr_waiting_phone (doctor_id, clinic_id, queue_date, phone, status)
)
```

This table backs the (currently unused-by-`process_checkin`) pure wait-list helpers
`_enqueue_overflow` / the `queue_position`/`estimated_time` fields on `QrCheckinResult`.
Everything else this module touches — `patients.tmpregtoken`, `patients.hospital_group_code`,
`patients.booking_id`, the appointment table's `channel` column, `clinics.hospital_group_code`,
`doctor_clinic_schedule.status` — is read/written conditionally (via
`booking_repository._column_exists` / `_table_columns` checks) but never created by
this module; those columns are expected to already exist via the main booking schema
migrations owned by `booking_repository`.

Back to [root README](../../README.md).
