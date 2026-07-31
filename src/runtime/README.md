<!-- src/runtime/README.md -->
# Runtime / Reliability / Multi-Channel Delivery Layer

This directory is the plumbing that sits between "a webhook fired" and "the FSM turn
actually ran and a reply went out on the right channel." It exists because a chat webhook
handler cannot simply call the LLM/FSM synchronously and return the reply in the HTTP
response: providers (Twilio, Meta, Infobip, Telegram) expect a fast 200 OK, a slow LLM
turn (Ollama) must never block a second message from the same user or from other users,
retries must not create duplicate replies, and a burst of impatient re-taps from one
patient must collapse into one turn instead of flooding the FSM. The strategy implemented
across these files, top to bottom, is:

1. **Ack fast** — every inbound webhook handler returns `200 OK` immediately; the actual
   turn is handed off to a background worker instead of being awaited inline
   (`src/api/webhooks.py`, using the pieces below).
2. **Dedup by provider SID** — before anything else runs, the inbound `MessageSid`
   (Twilio/Meta/Infobip) is checked against `message_sid_store.py`'s persistent set; a
   repeat delivery of the same webhook is dropped silently.
3. **Serialize per user** — `user_processing_guard.py` ensures only one turn is "in
   flight" for a given scoped user at a time, using a distributed Redis lock (so it works
   across multiple app instances) with an in-process fallback lock when Redis is down.
4. **Buffer/collapse bursts** — if a user is already being processed, the new message
   doesn't get dropped or run concurrently; it lands in `user_turn_buffer.py`'s bounded
   per-user deque, where near-duplicate low-intent messages (repeated "hi", "?", etc.)
   collapse into one, and higher-intent messages (booking > availability > greeting) win
   when collapsing.
5. **Bounded worker pool with retry + timeout** — accepted turns are handed to
   `turn_queue.py`'s `TurnQueueProcessor`, a fixed pool of worker threads pulling off a
   bounded `queue.Queue`. Failures get exponential-backoff retries; turns that blow past
   `PROCESSING_TIMEOUT_SECONDS` get a one-time "still working on it" message instead of a
   retry (retrying while the LLM is still busy would just produce a second timeout).
6. **Optional Kafka fronting** — `kafka_turn_bridge.py` and `kafka_notification_bridge.py`
   can sit in front of the in-process queue and the notification scheduler respectively,
   letting several app instances share one turn/notification stream for horizontal
   scale-out. Both are fully optional: disabled, unconfigured, or a failed `kafka-python`
   import all fall straight through to direct in-process processing.
7. **DB overflow as last resort** — if the in-process queue is completely full (all
   `QUEUE_WORKER_COUNT` workers busy, `QUEUE_MAX_SIZE` backlog full), `background_workers.py`'s
   `run_overflow_turn_poll_loop` drains a persistent MySQL `inbound_turn_queue` table
   (fed via `ConversationRepository.enqueue_overflow_turn`), reclaiming turns with a
   lease-based `SKIP LOCKED` claim and resubmitting them once capacity frees up.

Once a turn finishes, replies go out through `channel_delivery.py`'s `ChannelDelivery`,
which is the single abstraction every send path (normal reply, timeout-safe message,
automation/reminder message, document) funnels through, auto-detecting the right
WhatsApp provider (Twilio / Meta / Infobip) or Telegram per channel account.
Two more independent pieces round out the module: `account_scope.py`, the tiny
`acct:{channel_account_id}|{raw_user_id}` scoping scheme that every dict/lock/buffer/guard
above keys on for multi-tenant isolation, and `sms_notification_service.py`, a completely
separate SMS side-channel (confirmation/cancellation/reschedule texts) with its own
credit-reservation protocol against an external billing API.

Back to [root README](../../README.md).

---

## Big picture

```
Inbound webhook (src/api/webhooks.py)
  │
  ├─ 1. message_sid_store.seen_or_add(sid)   → duplicate? stop, return 200
  │
  ├─ 2. user_processing_guard.acquire(scoped_user_id)
  │        │
  │        ├─ acquired  → TurnTask ──► turn_queue / kafka_turn_bridge.submit()
  │        │                                   │
  │        │                                   ▼
  │        │                         queue.Queue(maxsize=QUEUE_MAX_SIZE)
  │        │                                   │  (QUEUE_WORKER_COUNT worker threads)
  │        │                                   ▼
  │        │                    process_fn (FSM turn) ──► channel_delivery.send_*()
  │        │                                   │                    │
  │        │                          watchdog thread          Twilio / Meta /
  │        │                       (PROCESSING_TIMEOUT_SECONDS)  Infobip / Telegram
  │        │                                   │
  │        │                    timeout → one "still processing" msg (no retry)
  │        │                    other error → retry with backoff, up to
  │        │                                  QUEUE_RETRY_ATTEMPTS
  │        │                                   │
  │        │                    queue full? → conversation_repository
  │        │                                  .enqueue_overflow_turn()
  │        │                                  (MySQL inbound_turn_queue,
  │        │                                   drained by background_workers.py)
  │        │
  │        └─ not acquired → user_turn_buffer.push(task)  (bounded deque,
  │                            collapses flood/low-intent duplicates)
  │                            when the in-flight turn finishes, on_success/
  │                            on_failure release the guard and pop_next()
  │                            from the buffer to submit the next turn
  │
  └─ scoped_user_id = account_scope.build_scoped_user_id(channel_account_id, raw_id)
     used as the key for the guard, the buffer, session state, and every
     per-user dict in main.py
```

Files in this directory:

| File | Role |
|---|---|
| `turn_queue.py` | Bounded multi-worker queue that actually runs FSM turns, with retry + per-task timeout watchdog |
| `user_processing_guard.py` | Per-user "only one turn in flight" lock (Redis-backed, local fallback) |
| `user_turn_buffer.py` | Per-user bounded buffer that holds/collapses messages while a turn is in flight |
| `message_sid_store.py` | Persistent, self-trimming set of seen provider `MessageSid`s (dedup) |
| `channel_delivery.py` | Unified outbound send path for Twilio/Meta/Infobip WhatsApp + Telegram |
| `kafka_turn_bridge.py` | Optional Kafka fronting for turn submission/consumption across instances |
| `kafka_notification_bridge.py` | Optional Kafka fronting for the automation/notification scheduler |
| `sms_notification_service.py` | Independent SMS delivery + credit reserve/release for appointment events |
| `account_scope.py` | `acct:{channel_account_id}|{raw_user_id}` scoping helpers used everywhere above |
| `background_workers.py` | Daemon-thread poll loops: MySQL overflow-queue drain, doctor-cache invalidation drain |
| `__init__.py` | Re-exports `PersistentMessageSidStore`, `TurnQueueProcessor`, `TurnTask` |

All of the classes here are constructed once as module-level singletons in
`MessageBot/main.py` and injected into `src/api/webhooks.py` (mostly as zero-arg lambdas,
so route closures always see the live singleton rather than one captured at import time).

---

## `turn_queue.py` — `TurnQueueProcessor`, `TurnTask`

This is the actual execution engine for FSM turns.

### `TurnTask`

A plain dataclass carrying everything a worker needs to process and reply to one inbound
message: `from_number` (the scoped user id), `body`, `inbound_sid`, `pre_state`,
`attempt` (retry counter, starts at 0), `enqueue_ts`, and two `threading.Event`s —
`send_started` and `timeout_notified` — that coordinate with the timeout watchdog
described below.

### `TurnQueueProcessor`

Constructed with:

- `worker_count` — from `settings.queue_worker_count` (env `QUEUE_WORKER_COUNT`, default
  `3`). `start()` spawns exactly this many daemon threads (`turn-worker-1`, `turn-worker-2`,
  …), each running `_worker_loop()`: block on `queue.get()`, run the task, `task_done()`,
  repeat. `stop()` pushes one `None` sentinel per thread and joins with a 2s timeout.
- `max_queue_size` — from `settings.queue_max_size` (env `QUEUE_MAX_SIZE`, default `60`).
  Backs a bounded `queue.Queue(maxsize=max_queue_size)`. `submit()` uses `put_nowait()`
  and returns `False` on `queue.Full` instead of blocking — callers (webhook handlers,
  the buffer's `pop_next` chain, the Kafka bridge, the overflow poller) are expected to
  react to a `False` return by buffering the task elsewhere or persisting it to the DB
  overflow queue.
- `process_fn` / `send_fn` — injected callables: `process_fn(from_number, body)` runs the
  FSM turn and returns either `(reply, post_state)` or `(reply, post_state, fsm)`;
  `send_fn(...)` delivers the reply. `TurnQueueProcessor` inspects `send_fn`'s signature
  at construction time (`inspect.signature`) to detect whether it accepts 5 arguments
  (including the `fsm` object) or the legacy 4, so it can call it correctly either way.
- `retry_attempts` — from `settings.queue_retry_attempts` (env `QUEUE_RETRY_ATTEMPTS`,
  default `2`), i.e. up to 2 retries after the first attempt (3 total).
- `processing_timeout_seconds` — from `settings.processing_timeout_seconds` (env
  `PROCESSING_TIMEOUT_SECONDS`, default `2.5`).
- `timeout_fn`, `on_success`, `on_failure` — callback hooks (see below).

### Retry with backoff

On any exception from `process_fn`/`send_fn` other than a `TimeoutError`, if
`task.attempt < retry_attempts` and the processor isn't stopping, the task is retried:
`backoff_seconds = min(4.0, 0.8 * (2 ** task.attempt))` (0.8s, 1.6s, 3.2s, capped at 4s),
scheduled via a `threading.Timer` that calls `_submit_retry_task`, which just calls
`submit()` again (re-entering the same bounded queue). `on_failure(task, exc, True,
backoff_seconds)` fires so callers can log/track the retry without releasing the
per-user guard yet. If the queue is full at retry time, that's treated as a final
failure (`on_failure(task, err, False, 0.0)`).

### The per-task watchdog thread and `PROCESSING_TIMEOUT_SECONDS`

If `timeout_fn` is set and `processing_timeout_seconds > 0`, `_run_task()` spawns one
extra daemon thread per task (`turn-timeout-{inbound_sid}`) that waits on a local
`done_event` for up to `processing_timeout_seconds`. If the task finishes in time, the
watchdog just returns. If it times out, the watchdog gives the send path one more short
grace window (`task.send_started.wait(timeout=0.75)` — `_timeout_grace_seconds`) in case
the reply is being sent at that exact instant; if it's still not sending and no timeout
notice has gone out yet (`task.timeout_notified`), it calls `timeout_fn(task,
TimeoutError(...))` exactly once.

**Why send a "still processing" message instead of retrying on timeout:** the retry
decision explicitly excludes timeouts — `is_timeout = isinstance(exc, TimeoutError)` and
`can_retry = not is_timeout and ...` — with the code comment *"Timeouts are NOT retried —
Ollama is still busy, retrying causes another timeout."* Resubmitting the same turn while
the LLM backend is still grinding on it would just queue up (or race) another call into
the same busy model and produce a second timeout — it doesn't help, it just burns another
worker slot and another `PROCESSING_TIMEOUT_SECONDS` window. Instead the watchdog sends a
one-shot "still working on it" notice (via `timeout_fn`, wired in `main.py` to
`_handle_turn_timeout`, which sends a localized "processing delay" message through
`channel_delivery`) and lets the *original* attempt keep running in the background. If
that original attempt eventually completes, `_run_task` still calls `send_fn` normally —
so the user can end up receiving the timeout notice followed by the real answer.
`timeout_notified` guards against sending the timeout notice twice (once from the
watchdog, once from the final-failure path if retries are also exhausted).

### Success/failure callback hooks

- `on_success(task)` fires after a reply is sent successfully. In `main.py` this releases
  the user's processing-guard lock, pops and submits the next buffered turn for that
  user, and (if the task came from the DB overflow queue) marks the overflow row `DONE`.
- `on_failure(task, exc, will_retry, backoff_seconds)` fires on both a scheduled retry
  (`will_retry=True`) and a final failure (`will_retry=False`). In `main.py`, the guard is
  only released and the next buffered turn only submitted on final failure — a task that's
  about to retry should keep holding the per-user slot. Overflow-queue rows are updated
  via `mark_overflow_turn_retry` only on final failure (attempt count bumped, row flips to
  `RETRY` or `DEAD` depending on max attempts).

### Metrics

`snapshot()` returns `worker_count`, `alive_workers`, `backlog_size`, `submitted`,
`dropped`, `processed`, `retried`, `failed`, `retry_attempts`, and
`processing_timeout_seconds` — useful for a health/status endpoint.

---

## `user_processing_guard.py` — `UserProcessingGuard`

Ensures at most one turn is "in flight" per scoped user, so a burst of messages from the
same person never runs the FSM concurrently against the same session state.

- **Redis path (works across instances):** `acquire(user_id)` does a Redis `SET key 1 NX
  EX lock_ttl_seconds` on `{key_prefix}:proc:{user_id}` (constructed in `main.py` with
  `lock_ttl_seconds` from env `REDIS_PROCESSING_TTL_SECONDS` (default 45),
  `key_prefix` from env `REDIS_KEY_PREFIX` (default `msgbot`)). `NX` means the lock only
  succeeds if no other process/instance currently holds it — this is what makes the guard
  correct when the bot runs as multiple app instances behind a load balancer, not just
  multiple threads in one process. `release(user_id)` deletes the key.
- **In-process fallback (fail-open when Redis is down):** if no Redis client was ever
  configured, or a Redis call raises, `_local_acquire`/`_local_release` fall back to a
  plain `dict[user_id] -> monotonic acquire timestamp` guarded by a `threading.Lock`,
  with the same TTL semantics (an entry older than `lock_ttl_seconds` is treated as
  stale/expired and can be re-acquired). Every `acquire()` call actually primes the local
  fallback lock *in parallel* with the Redis attempt (the local acquire is evaluated
  eagerly as the `default=` argument passed into `_call_redis`), so the local guard state
  stays warm and consistent even while Redis is healthy — if Redis then starts failing
  mid-session, the fallback is already correctly synchronized rather than starting cold.
- **Fail-open cooldown (`_redis_disabled_until`):** `_call_redis()` first checks
  `_redis_disabled()` — if the current monotonic time is before `_redis_disabled_until`,
  it skips calling Redis entirely and returns the fallback `default` immediately. Any
  exception from an actual Redis call sets `_redis_disabled_until = now +
  fail_open_cooldown_seconds` (default 2.0s, tunable via constructor). This prevents every
  single inbound message from re-attempting (and re-timing-out against) a dead or
  overloaded Redis instance — once a failure is observed, the guard "gives up" on Redis
  for a short cooldown window and serves purely off the local dict, then automatically
  tries Redis again once the cooldown expires. Redis operations also carry their own
  short timeout (`redis_op_timeout_ms`, default 40ms via `socket_timeout`/
  `socket_connect_timeout` set in `build_redis_client_from_env`) so a single slow call
  can't stall the webhook handler.
- **`allow_busy_hint(user_id)`:** a separate, Redis-only rate limiter (`SET NX EX
  busy_ttl_seconds` on `{key_prefix}:busy:{user_id}`, `busy_ttl_seconds` from env
  `REDIS_BUSY_HINT_TTL_SECONDS`, default 8) intended to let a caller send at most one
  "please wait, I'm still replying to your last message" notice per `busy_ttl_seconds`
  window instead of one per buffered message. It always returns `False` when Redis isn't
  configured (no local fallback for the hint — the design tolerates dropping this
  cosmetic feature under Redis outage, unlike the correctness-critical lock itself).
- `build_redis_client_from_env()` constructs the shared `redis.Redis` client from
  `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `REDIS_SOCKET_TIMEOUT_MS`,
  `REDIS_CONNECT_TIMEOUT_MS`, pings it once at startup, and returns `None` (triggering the
  local-only fallback everywhere) if the `redis` package is missing or the ping fails.

---

## `user_turn_buffer.py` — `UserTurnBuffer`

Holds messages for a user whose previous turn is still being processed (i.e.
`UserProcessingGuard.acquire()` returned `False`), instead of dropping them or letting
them race the in-flight turn.

- **Bounded per-user deque:** `max_per_user` (env `PER_USER_QUEUE_MAX`, default `5`).
  `push()` appends to a `collections.deque` keyed by scoped user id; once the deque
  exceeds `max_per_user`, the oldest entry is dropped (`popleft()`,
  `dropped_oldest=True` in the result) — a deliberately lossy "keep the most recent
  N" policy rather than growing unbounded for an abandoned or spammy conversation.
- **Flood/intent-aware collapsing within `PER_USER_COALESCE_WINDOW_SECONDS`** (default
  `6.0`, from env `PER_USER_COALESCE_WINDOW_SECONDS`): two collapsing paths exist.
  - *Against the already-dispatched turn:* `record_dispatch(user_id, body)` is called
    right when a task is handed to a worker, stamping `(body, now)`. If a *new* message
    arrives within the window and `_bodies_are_flood()` says it's noise relative to the
    dispatched body, it's dropped silently (`collapsed=True`) — the in-flight turn is
    already handling this user, no need to queue a near-duplicate.
  - *Against the buffer's own tail:* if the last buffered task for this user is also in
    `INIT` state, within the window, and flood-equivalent to the incoming message, the
    incoming message replaces it in place (or is dropped) rather than growing the deque.
  - `_bodies_are_flood(a, b)` treats two messages as the same flood either when their
    normalized text is identical, or when *both* are independently low-priority
    (`_init_intent_priority == 0`, i.e. greetings or ≤2-word messages) — two different
    one-word greetings still collapse together.
- **Priority ordering when collapsing:** `_init_intent_priority(text)` scores a message
  3 for booking-intent tokens (`book`, `appointment`, `chahiye`, Hindi बुक/अपॉइंटमेंट/etc.),
  2 for availability-intent tokens (`availability`, `slot`, `khali`, Hindi स्लॉट/खाली/etc.),
  0 for bare greetings or ≤2-word messages, else 1. When collapsing against the buffer's
  tail, the higher-priority message wins and replaces the queued one (`new_priority >=
  old_priority`) — so if a user sends "hi" then immediately "book appointment", the
  buffer keeps the booking message, not the greeting.
- `push_front()` / `pop_next()`: used by the guard-release chain in `main.py`
  (`_submit_next_buffered_turn`) — when a turn finishes, the next buffered task is popped
  and resubmitted; if the guard can't be re-acquired or the queue is full, the task is
  pushed back to the *front* of its user's deque so ordering is preserved and it's the
  next thing tried.

---

## `message_sid_store.py` — `PersistentMessageSidStore`

Deduplicates inbound messages by provider `MessageSid` (Twilio/Meta/Infobip) so that a
webhook retry (all these providers retry on non-2xx or timeout) never triggers a second
FSM turn for the same physical message.

- Backed by a JSONL file (`data/seen_message_sids.jsonl` in `main.py`'s wiring), one
  `{"sid": ..., "ts": ...}` row per line, loaded fully into memory at startup: an
  ordered `deque[str]` (for trimming) plus a `set[str]` (for O(1) membership checks).
- `seen_or_add(sid)` is the single entry point: returns `True` (already seen — caller
  should drop the message) if the sid is already in the in-memory set; otherwise adds it
  to both the set and the deque, appends a new JSON line to the file, and returns `False`.
  All access is guarded by a single `threading.Lock`.
- **Self-trimming to `max_entries` (default `50000`):** every `seen_or_add()` call
  triggers `_trim_if_needed(force=False)`. Once the in-memory deque exceeds
  `max_entries`, the oldest entries are popped from both the deque and the set until it's
  back under the cap.
- **Periodic rewrite:** trimming the in-memory structures doesn't shrink the file on
  every call — a full rewrite (`_dirty_count` reset, file rewritten with only the
  surviving sids) only happens once at least 100 new entries have been appended since the
  last rewrite (`_dirty_count >= 100`), or unconditionally at load time (`force=True`).
  This keeps disk I/O to periodic bulk rewrites instead of a full-file rewrite on every
  single inbound message.

---

## `channel_delivery.py` — `ChannelDelivery`

The single outbound-send abstraction. Every reply path in the app — the normal FSM turn
reply, the timeout-safe "still processing" notice, automation/reminder messages, document
sends — goes through one `ChannelDelivery` instance instead of each caller talking to
Twilio/Meta/Infobip/Telegram directly.

### Provider auto-detection

`_provider_for_account(account)` decides which WhatsApp provider to use for a given
inbound/outbound message, resolved per-account first, then falling back to global env
config:

1. If the resolved `channel_account` row itself carries a `provider`/`_provider` value in
   `{"meta", "infobip", "twilio"}`, use it directly — this is the **per-account credential
   override**, resolved via `channel_account_lookup_fn` (an injected callable, wired in
   `main.py` to `channel_account_repository.get_account_by_id`), keyed off the
   `channel_account_id` embedded in the scoped user id (see `account_scope.py`).
2. Else if `settings.whatsapp_provider` (env `WHATSAPP_PROVIDER`) is explicitly `meta`,
   `infobip`, or `twilio`, use that.
3. Else (`WHATSAPP_PROVIDER=auto`, the default) — probe for credentials in order: Meta
   (account-level `whatsapp_api_token`/`meta_access_token` + phone-number-id), then
   Infobip (account-level API key + base URL + WhatsApp number), then Twilio
   (account-level SID + auth token), then fall through to the equivalent global env
   credentials (`WHATSAPP_API_TOKEN`/`WHATSAPP_PHONE_NUMBER_ID`, `INFOBIP_API_KEY`/
   `INFOBIP_BASE_URL`/`INFOBIP_WHATSAPP_NUMBER`, or a configured global Twilio client +
   `twilio_whatsapp_from`).

### Twilio

`send_whatsapp_response()`/`send_plain_channel_message()` use the Twilio SDK
(`twilio_client.messages.create(...)`) through `send_with_retries()`, which retries up to
`settings.twilio_send_retries + 1` total attempts (env `TWILIO_SEND_RETRIES`, default `2`
→ 3 attempts) on any exception, re-raising the last error if all attempts fail. It
supports per-account Twilio clients (`_twilio_client_for_account` builds a fresh
`twilio.rest.Client` from account-level SID/auth-token when present, else reuses the
global client) and an optional `status_callback` URL (per-account or global
`TWILIO_STATUS_CALLBACK_URL`).

**WhatsApp template support** for four FSM states is built in: `template_for_state(state)`
maps `ASK_PHONE` / `ASK_CLINIC` / `ASK_DATE` / `ASK_TIME` to the corresponding
`settings.twilio_template_*_sid` (Twilio Content API template SIDs). When a template
applies, `content_variables_for_state(state, fsm)` supplies the `{"1": ..., "2": ...,
"3": ...}` content variables Twilio needs to fill the template's quick-reply buttons —
for `ASK_DATE` it's the FSM's next three date options (`fsm._date_options()`), for
`ASK_TIME` its next three suggested slots (`fsm._suggested_slots()`); if fewer than 3
options are available the template is skipped and a plain-text message is sent instead.

### Meta

`_send_meta_whatsapp_text()` POSTs directly to `https://graph.facebook.com/{version}/
{phone_number_id}/messages` via raw `urllib.request` (no SDK), with `Authorization:
Bearer {token}` and a JSON `{"messaging_product": "whatsapp", "type": "text", "text":
{"body": ...}}` payload. `version` defaults to `v21.0` (env
`WHATSAPP_GRAPH_API_VERSION`), destination numbers are normalized to digits-only via
`_normalize_meta_to_number` (strips a `whatsapp:` prefix if present, then strips
non-digits).

### Infobip

`_send_infobip_whatsapp_text()` similarly POSTs raw JSON via `urllib.request` to
`{INFOBIP_BASE_URL}/whatsapp/1/message/text`, with `Authorization: App {api_key}` and a
`{"from": sender, "to": ..., "content": {"text": ...}}` payload.

### Telegram

`send_telegram_message()`/`send_telegram_document()` talk to
`https://api.telegram.org/bot{token}/sendMessage` and `.../sendDocument` via raw HTTPS
(`urllib.request`), with the document send hand-rolling a `multipart/form-data` body
(no `requests` dependency needed). Text messages use `parse_mode: HTML`, and
`_format_telegram_text()` does **basic HTML-bold formatting**: it HTML-escapes the whole
body, then regex-converts specific `*label: value*`-style fields the FSM emits (English
and Hindi variants of `Appointment ID:`, `Clinic:`, `Date:`, `Time:`, `Patient ID:`) into
`<b>...</b>` tags — it is not a general Markdown-to-HTML converter, just enough to bold
the handful of structured fields the bot's message templates use.

`resolve_telegram_bot_username(account)` calls Telegram's `getMe` endpoint to fetch the
bot's `@username` for a given token. This is used for **Telegram bot-username
auto-resolution at startup**: `main.py`'s `startup_validation()` calls
`_resolve_telegram_bot_username()` (which delegates here) once at boot and caches the
result in `_telegram_bot_username_runtime`, so the bot doesn't need the username
hardcoded in config — it discovers it from the token it already has (per-account sender
identity resolution is still required for multi-bot routing, per the log message emitted
when this resolves to nothing).

---

## `kafka_turn_bridge.py` and `kafka_notification_bridge.py`

Both are **optional** horizontal-scale-out fronting layers with an identical
fail-through design: if disabled, unconfigured, or the `kafka-python` import fails, they
transparently fall back to direct/local processing with no behavior change to the caller.

- **Enabled check:** both compute `_enabled = settings.kafka_enabled and
  bool(settings.kafka_bootstrap_servers)` at construction (env `KAFKA_ENABLED`,
  `KAFKA_BOOTSTRAP_SERVERS`). `_load_kafka_classes()` tries `from kafka import
  KafkaConsumer, KafkaProducer`; on any import failure it logs a warning, force-sets
  `_enabled = False`, and returns `(None, None)`. `start()` additionally catches any
  exception from actually constructing the producer/consumer (e.g. can't reach the
  brokers) and disables itself the same way — so a Kafka outage at boot degrades to
  single-instance operation instead of crashing the app.

### `KafkaTurnBridge` — fronts `TurnQueueProcessor`

- Wraps a `TurnQueueProcessor` instance (`_turn_processor`, i.e. `_base_turn_processor`
  in `main.py`) and exposes the same `submit()`/`backlog_size()`/`snapshot()` surface, so
  `main.py` can hand `turn_processor = KafkaTurnBridge(...)` to `webhooks.py` and
  `background_workers.py` without either caring whether Kafka is actually in play.
- **What it publishes:** `submit(task)` (and `submit_overflow(task)`, an identical alias
  used by the DB overflow poller) serializes the `TurnTask` to JSON (`from_number`,
  `body`, `inbound_sid`, `pre_state`, `attempt`, `enqueue_ts`) and publishes it to the
  turn topic — default `msgbot.turns` (env `KAFKA_TURN_TOPIC`), keyed by
  `inbound_sid or from_number`, waiting synchronously (`future.get(timeout=5)`) for the
  broker ack (`acks="all"`). If Kafka isn't enabled, or the publish itself raises, it
  falls straight back to `self._turn_processor.submit(task)` — the in-process queue.
- **What it consumes:** `start()` also always calls `self._turn_processor.start()` (the
  local worker pool runs regardless), and — only if Kafka is enabled — starts a
  `kafka-turn-consumer` daemon thread polling the same topic (`group_id` default
  `msgbot-turn-workers`, env `KAFKA_TURN_CONSUMER_GROUP`; `enable_auto_commit=False`,
  manual commit only after the local queue accepted the task) and feeding deserialized
  tasks into the local `TurnQueueProcessor`, retrying (`sleep(0.2)`) if the local queue is
  momentarily full rather than dropping.
- **Purpose:** with Kafka enabled, every app instance both publishes turns it receives via
  webhook *and* consumes turns from the shared topic (including ones published by other
  instances) into its own local worker pool — turning N independent single-instance queues
  into one shared, horizontally-scaled turn pipeline.

### `KafkaNotificationBridge` — fronts the automation/reminder notification path

- Same enable/fallback shape, default topic `msgbot.notifications` (env
  `KAFKA_NOTIFICATION_TOPIC`), consumer group default `msgbot-notification-workers` (env
  `KAFKA_NOTIFICATION_CONSUMER_GROUP`). Wired in `main.py` onto
  `automation_scheduler._notification_bridge`.
- **What it publishes/consumes:** `process_pending_events(events)` takes a batch of
  notification events (appointment reminders, doctor-delay alerts, etc. —
  `NotificationEvent` from `src.repositories.notification_repository`) and, per event,
  either publishes it to Kafka (falling back to calling `process_event_fn(event)` — the
  scheduler's direct-send path — if Kafka is disabled or the publish fails) or, if
  disabled outright, always calls `process_event_fn(event)` directly. The consumer thread
  mirrors the turn bridge's pattern: poll, deserialize, call `process_event_fn`, commit.
- **Purpose:** identical rationale to the turn bridge — let multiple scheduler instances
  (each running `AutomationScheduler`) share one notification stream instead of each
  instance only being able to act on notifications it personally discovered.

---

## `sms_notification_service.py` — `SMSNotificationService`

An intentionally **independent** side-channel for appointment SMS notifications — the
class docstring is explicit: *"Completely isolated - no database access, no FSM
knowledge. Configuration driven from .env settings."* It only knows how to build message
text and make outbound HTTP calls; callers (the automation scheduler) supply all
appointment data and decide when to invoke it.

- **Message building:** `build_message_by_event_type(event_type, ...)` dispatches to
  `build_confirmation_message` / `build_cancellation_message` / `build_rescheduled_message`
  for `CONFIRMATION` / `CANCELLED` / `RESCHEDULED` (any other `event_type` falls back to a
  generic "Appointment update: ..." message). Each formats the date/time via
  `_format_display_date`/`_format_display_time` (tries a few input formats, renders as
  e.g. `25 Apr 2026` / `2:30 PM`) and appends a `Manage: {frontend_base_url}` link and a
  `- Dapto` signature.
- **Sending:** `send_sms(phone_number, message)` is outbound-only — it builds a query-
  string GET-style URL against a generic HTTP SMS gateway (`sms_api_url`, env
  `SMS_API_URL`) with `sender`, `numbers`, `messagetype` (default `TXT`), `message`,
  `response` (default `Y`), and `apikey` (env `SMS_API_KEY`) parameters, and treats HTTP
  200 as success, extracting a provider message id from the JSON response body via
  `_extract_provider_message_id`. Phone numbers are normalized to digits-only.
- **Gating — `SMS_ENABLED` + `SMS_ENABLED_CHANNELS`:** `send_sms()` itself short-circuits
  if `sms_enabled` (env `SMS_ENABLED`, default `false`) is falsy. Separately,
  `is_sms_enabled_for_channel(channel)` checks whether the appointment's *source booking
  channel* (e.g. `qr_scan`, `whatsapp_web`, `app`, `web`) is present in
  `enabled_channels`, parsed from the comma-separated env `SMS_ENABLED_CHANNELS` (e.g.
  `qr_scan,web`) — an appointment booked through a channel not in that list never gets an
  SMS, independent of the master `SMS_ENABLED` switch. Both gates are meant to be checked
  by the caller before invoking send.
- **Credit reserve/release protocol:** `send_sms_with_credit_check(doctor_id,
  appointment_id, phone_number, message)` wraps the raw send with a 3-step protocol
  against an external billing API at `https://dapto.vinfocom.co.in`:
  1. `reserve_sms_credit(doctor_id, appointment_id)` — `POST
     /api/internal/doctors/{doctor_id}/sms-consume` with `{"appointmentId":
     appointment_id}`, authenticated via header `X-Internal-API-Key` (value from
     `settings.x_internal_api_key`, env `X_INTERNAL_API_KEY`, falling back to env
     `INTERNAL_API_KEY`). Success requires the response's `success` flag plus either
     `reserved` or `alreadyConsumed` (the latter makes the reserve step idempotent against
     retries); failure reasons (`CREDITS_EXHAUSTED`, `API_TIMEOUT`, etc.) are surfaced to
     the caller so it never sends SMS without a reserved credit — *"In production, SMS
     must never be sent unless credit was reserved successfully"* per the inline comment.
  2. If reserved, `send_sms()` runs.
  3. If the send fails after a successful reservation, `release_sms_credit(doctor_id,
     appointment_id)` — `POST /api/internal/doctors/{doctor_id}/sms-release` with the
     same auth header — gives the credit back so a failed delivery doesn't silently burn
     the doctor's SMS quota.

---

## `account_scope.py`

Two pure functions implementing the multi-tenant user-scoping scheme used throughout the
runtime layer and beyond (session state, locks, buffers, per-user dicts in `main.py`):

- `build_scoped_user_id(channel_account_id: int, raw_user_id: str) -> str` produces
  `f"acct:{channel_account_id}|{raw_user_id}"`, e.g. `acct:42|whatsapp:+15551234567` or
  `acct:7|telegram:123456789`.
- `parse_scoped_user_id(value: str) -> Tuple[Optional[int], str]` reverses it: if `value`
  doesn't start with `acct:`, it's treated as an already-raw (unscoped) id and returned as
  `(None, value)`; otherwise it splits on the first `|`, parses the `acct:` prefix as an
  int, and returns `(channel_account_id, raw_user_id)` — or `(None, value)` if the prefix
  isn't a valid integer.

Every downstream consumer keys off this scoped id rather than the raw phone number/chat
id: `UserProcessingGuard`, `UserTurnBuffer`, `TurnTask.from_number`, `ChannelDelivery`
(which calls `parse_scoped_user_id` to both resolve the owning `channel_account` for
provider/credential lookup and recover the raw destination address to actually send to),
and the session/FSM state keys in `main.py`. This is what lets one deployment safely host
many doctors/clinics behind many WhatsApp numbers and Telegram bots without one patient's
conversation state or in-flight lock colliding with another account's same raw phone
number.

---

## `background_workers.py`

Two independent poll loops, each meant to run as a single daemon thread per app instance,
started from `main.py`'s `startup_validation()` and stopped via a `threading.Event` in
`shutdown_workers()`.

### `run_overflow_turn_poll_loop` — MySQL overflow-queue drain

No-ops immediately if `conversation_repository` is `None` (DB booking disabled). Otherwise,
in a loop until `overflow_poll_stop` is set:

1. **Periodic SID purge:** at most once every `sid_purge_interval_seconds` (env
   `INBOUND_SID_PURGE_INTERVAL_SECONDS`, default `3600`), calls
   `conversation_repository.purge_old_message_sids(retention_days=sid_retention_days)`
   (env `INBOUND_SID_RETENTION_DAYS`, default `30`) to delete stale rows from the
   DB-side inbound-SID dedup table, keeping it from growing forever.
2. **Claim:** `conversation_repository.claim_overflow_turns(limit=claim_size,
   worker_id=overflow_worker_id)` where `claim_size = settings.queue_worker_count`. This
   runs a `SELECT ... FOR UPDATE SKIP LOCKED` against the `inbound_turn_queue` table for
   rows in `PENDING`/`RETRY` status whose `next_retry_at` has passed and whose
   `locked_at` is either unset or stale (older than 5 minutes — a lease-expiry pattern
   that lets another worker reclaim rows from a crashed poller), then flips their status
   to `PROCESSING` with `lock_owner = overflow_worker_id`
   (`overflow_worker_id = f"overflow-{uuid4().hex[:10]}"`, unique per process). If nothing
   is claimed, the loop waits `0.8s` and retries.
3. **Resubmit:** each claimed row becomes a `TurnTask` (carrying its stored
   `attempt_count`) and is handed to `turn_processor.submit_overflow(task)` if that method
   exists (the `KafkaTurnBridge` exposes it as an alias for `submit`), else
   `turn_processor.submit(task)` — i.e. it re-enters the exact same live pipeline
   (in-process queue or Kafka) as a fresh webhook-originated turn, not a separate code
   path. If accepted, `track_overflow_task(task, row.queue_id)` records the
   `inbound_sid -> queue_id` mapping (in `main.py`'s `_overflow_turn_map`) so the
   `TurnQueueProcessor`'s `on_success`/`on_failure` hooks can later mark the row `DONE` or
   `RETRY`/`DEAD` in the DB. If rejected (the live pipeline is *still* full), the row is
   put back with `conversation_repository.release_overflow_turn(queue_id, reason="Runtime
   queue still full", backoff_seconds=settings.queue_overflow_requeue_backoff_seconds)`
   (env `QUEUE_OVERFLOW_REQUEUE_BACKOFF_SECONDS`, default `1.0`), which resets it to
   `RETRY` with a fresh `next_retry_at`.

This is the durable, cross-restart backstop beneath the in-memory `UserTurnBuffer`: rows
survive an app restart because they live in MySQL, whereas the in-process queue and buffer
are lost on crash.

### `run_doctor_cache_invalidation_loop` — Redis doctor-availability cache drain

No-ops if `scheduling_repository` is `None`. Otherwise, in a loop until `cache_inv_stop`
is set, claims up to `min(100, settings.queue_worker_count * 10)` pending cache-
invalidation events via `scheduling_repository.claim_cache_invalidation_events(limit,
worker_id=cache_inv_worker_id)` (`cache_inv_worker_id = f"dcache-{uuid4().hex[:10]}"`), or
waits `0.8s` if none are pending. For each claimed event it calls
`scheduling_repository.process_cache_invalidation_event(event)` (clears the relevant
Redis-cached doctor-availability entry — the cache itself is set up in `main.py` with TTL
from env `REDIS_DOCTOR_CACHE_TTL_SECONDS`, default `3600`) then
`mark_cache_invalidation_done(event.queue_id)`; on any exception it logs the event's
`entity_type`/`doctor_id`/`clinic_id` and calls `release_cache_invalidation(event.queue_id)`
to put the event back for another worker/attempt.

Both loops are started as named daemon threads in `main.py`
(`overflow-turn-poller`, `doctor-cache-invalidation-poller`) guarded by their own
`threading.Event`s (`_overflow_poll_stop`, `_cache_inv_stop`) so they can be cleanly
signaled to stop and joined (2s timeout each) during FastAPI's lifespan shutdown.

---

## `__init__.py`

Just a convenience re-export surface: `from src.runtime import PersistentMessageSidStore,
TurnQueueProcessor, TurnTask` works without reaching into the submodules directly. Other
classes in this package (`ChannelDelivery`, `UserProcessingGuard`, `UserTurnBuffer`,
`KafkaTurnBridge`, `KafkaNotificationBridge`, `SMSNotificationService`, the
`account_scope`/`background_workers` functions) are imported from their specific modules
where used, not re-exported here.
