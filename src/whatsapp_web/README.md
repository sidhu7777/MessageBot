# src/whatsapp_web

Despite the package name, this is **not** a WhatsApp integration. It is a plain server-rendered
HTML/JS booking widget — a small single-page app — that a patient opens in a regular browser. It
exists as the "landing page" that other channels link out to when a full booking UI (clinic picker,
date/time pickers, an appointment list, cancel/reschedule) is a better fit than a pure chat
back-and-forth. In this codebase that link is most often the one sent by the Evolution
auto-responder (see [src/evolution/README.md](../evolution/README.md)) after a patient's first
message, but the page is equally reachable via a QR code or any other shared URL — nothing about the
page itself assumes WhatsApp was involved.

Back to [root README](../../README.md).

## What actually renders — `page_renderer.py`

The whole widget is produced by one function, `render_whatsapp_web_page_html(*, doctor_id,
doctor_name, language="en", lock_language=False)`, which returns a complete, self-contained HTML
document (inline `<style>` and `<script>`, no build step, no external JS framework or CDN
dependency).

### Branding and localization

- `_dapto_logo_src()` lazily loads `Dapto_logo.jpeg` from the repo root (`parents[2]` from this file)
  and base64-encodes it into a `data:image/jpeg;base64,...` URI for the "Powered by" footer image,
  cached with `lru_cache`. If the file can't be read, the `<img>` is left with an empty `src` and
  hides itself via `onerror`.
- Three languages are supported: `en`, `hi`, `hinglish`. `language` picks the language the
  server renders with; if it's not one of the three, it falls back to `en`.
- Two copies of the UI text exist: a Python `ui` dict (used to fill the server-rendered HTML directly
  — labels, titles, button text) and a much larger `texts_json` dict (embedded into the page as a
  JS object `t`) used by client-side JS to relabel the page dynamically and to build
  status/modal/error copy that only exists client-side (loading states, validation messages, result
  summaries).
- `lock_language`: when `True`, the page still allows the visitor to have arrived with an explicit
  `?lang=` query param (set by the route, see below) and the client-side JS will not silently
  overwrite it via the `Accept-Language` header path — practically, the rendered `activeLanguage`
  starts pinned to `serverLang`.

### Page structure (sections)

The document is one `.card` containing a hero header (kicker + `Welcome to Dr. {doctor_name}
Clinic` title + subtitle) followed by up to four toggleable `<section>` blocks, only one of which is
visible at a time (others carry a `hidden` class):

1. **`identitySection`** (always visible first) — "Booking For" radio (`Self` / `Someone Else`),
   `Full Name` and `Phone Number` inputs, and a `Continue` button. This is the identity/lookup step.
2. **`existingSection`** — shown when the lookup finds active appointment(s) for that phone number.
   Renders one `.appt-card` per appointment (via the client-side `appointmentLine` template) with
   patient name, appointment ID, clinic, date, time, and `Reschedule` / `Cancel Appointment` buttons.
   Also has a `Book New Instead` button.
3. **`bookingSection`** — the new-booking form: clinic `<select>`, date `<select>`, an optional
   period `<select>` (morning/afternoon/evening, shown only when there are more than 4 distinct
   hour-slots to pick from), a time `<select>`, and a `Book Appointment` button.
4. **`rescheduleSection`** — the same clinic/date/period/time picker shape as booking, but for
   rescheduling a specific existing appointment (`Confirm Reschedule` button).

Two modals are always present in the DOM: `resultModal` (success/warning/error outcome after any
action — colored heading, formatted message body, appointment summary rows) and `confirmModal` (a
yes/no confirmation, used specifically before cancelling an appointment).

### Client-side flow (vanilla JS, no framework)

- `doLookup()` — fires on `Continue`. Validates name+phone are present, then `POST
  /whatsapp/web/lookup`. If the response has any `appointments`, it calls `renderExisting(rows)` to
  show `existingSection`; otherwise it calls `showBookingSection()` to go straight to the new-booking
  form.
- `showBookingSection()` / `showRescheduleSection(appointmentId)` — populate the clinic dropdown via
  `loadClinics()` (`GET /whatsapp/web/clinics`), then wire up cascading `change` listeners: choosing a
  clinic loads dates (`GET /whatsapp/web/dates`), choosing a date loads times (`GET
  /whatsapp/web/times`) which may come back in `mode: "periods"` (patient picks a period first, which
  then re-queries times filtered to that period) or `mode: "slots"` (times shown directly) — this
  period/slot logic itself lives server-side in `whatsapp_web_routes.py`'s `_grouped_time_payload`,
  not in this file; `page_renderer.py` just reacts to whichever `mode` comes back.
  For rescheduling, `loadTimes(...)` is called with `reschedule: true, appointmentId` so the times
  query excludes the current appointment's own slot from conflict checks.
- `submitBooking()` — `POST /whatsapp/web/book` with clinic/date/time/name/phone/`booking_for_self`;
  interprets `data.status` (`"booked"` -> ok modal, `"active_booking"` -> warn modal, anything else ->
  error modal) and shows a result summary (appointment ID, clinic, date, time).
- `submitReschedule()` — `POST /whatsapp/web/reschedule` with the pending appointment id plus the
  newly chosen clinic/date/time.
- `cancelAppointment(appointmentId)` — opens the confirm modal, and on confirmation `POST
  /whatsapp/web/cancel`, then re-runs `doLookup()` to refresh the appointment list.
- All requests go through a small `fetchJson` helper that throws on non-OK responses using the
  server's `detail`/`message` field, which the UI surfaces as an error modal.

The full JSON contract for `/whatsapp/web/clinics`, `/dates`, `/times`, `/lookup`, `/book`,
`/cancel`, and `/reschedule` (request/response shapes, status codes, query params) is documented in
[src/api/README.md](../api/README.md) — this file only describes how the widget consumes them, not
the endpoints themselves in full.

## Routing wiring — `src/api/whatsapp_web_routes.py`

`register_whatsapp_web_routes(app, *, booking_repository, scheduling_repository, logger)` registers:

- `GET /whatsapp/web?doctor_id=...` and `GET /whatsapp/web/{doctor_slug}` — both resolve a doctor
  (the slug route looks it up via `_resolve_doctor_id_by_slug`, matching the `slug` column used in
  `EvolutionRepository`'s `EvolutionDoctorContext.slug`) and call `_render_whatsapp_web_page`, which
  invokes `render_whatsapp_web_page_html(doctor_id=..., doctor_name=..., language=..., lock_language=...)`
  from this package — this is the only place `page_renderer.py` is used.
- The language served is resolved by `_resolve_effective_language`: an explicit `?lang=` query param
  wins (and locks it), otherwise it falls back to the `Accept-Language` header, otherwise `en`.
- The JSON endpoints (`/whatsapp/web/clinics`, `/dates`, `/times`, `/lookup`, `/book`, `/cancel`,
  `/reschedule`) that the rendered page's JS calls are defined in the same file.

## Booking bypasses the FSM entirely

Every booking/lookup/cancel/reschedule action in this widget goes straight through
`booking_repository` and `scheduling_repository` — the same repositories the conversational bot uses,
but called directly from these HTTP handlers. There is no FSM state machine, no turn buffer, no
intent classification involved; the widget is a fully separate, synchronous request/response path
into the same data.

Notable business rules enforced in `whatsapp_web_routes.py` (not in `page_renderer.py`, which is pure
presentation):

- **Same-day identity check** (`_find_same_day_identity_appointment`): before creating a new booking,
  the route looks for an existing active appointment (`BOOKED`/`PENDING`/`CONFIRMED`) for the same
  admin/doctor/date with a matching normalized name and phone number, and blocks the new booking with
  an `active_booking` status if found (`whatsapp_web_book`) — surfaced in `doLookup()`'s result too.
- **Self vs. someone-else identity checks**:
  - When booking `booking_for_self=True`, `_self_name_mismatch` looks up the name already on file for
    a `SELF` profile with that phone number and rejects the submission if the typed name doesn't
    match ("This phone number is linked to self name {self_name}...").
  - When booking `booking_for_self=False`, `_other_name_matches_self_name` rejects the submission if
    the typed "someone else" name is the same as the known self name for that phone
    ("Please use a different name other than self when booking for another person.").
- **Two-active-bookings cap**: both `whatsapp_web_lookup` and `whatsapp_web_book` call
  `booking_repository.list_active_appointments_by_phone_number(...)` and, for the "someone else" path,
  reject once there are already `>= 2` active appointments on that phone number and the new name
  doesn't match any existing booking's patient name — returning `_max_active_bookings_message(lang)`
  (`get_message(lang, "max_active_bookings_reached")` from `src/messages/templates.py`).
- **Reschedule conflict checks**: `_reschedule_conflict_exists` and `_list_reschedule_exact_times`
  make sure a rescheduled slot doesn't collide with another appointment's `start_time` on the same
  doctor/date before calling `booking_repository.reschedule_appointment_same_clinic`.

## Why this widget exists

The conversational FSM is good for a WhatsApp/chat-shaped back-and-forth, but some entry points don't
have a chat session to drive a state machine through — a first-time visitor scanning a QR code, or a
patient who has just received a booking link from the Evolution auto-responder, has no established
chat turn to react to yet. This widget is the fallback "just show me a form" experience for exactly
those cases: a self-contained page that can present the full clinic/date/time/appointment UI in one
shot and talk directly to the booking/scheduling repositories, independent of whatever channel (or no
channel at all) got the patient there.
