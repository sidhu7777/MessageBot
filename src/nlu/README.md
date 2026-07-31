# NLU + LLM Layer

This layer turns a raw inbound WhatsApp/Telegram message into a routing decision (intent, language, abuse flag) and, later in the conversation, into structured booking entities (name, phone, date, time, doctor, clinic, change targets). It is split across two packages that are really one logical unit: `src/nlu/` holds deterministic, regex/keyword-based classifiers and extractors plus the top-level router that orchestrates them, and `src/llm/` holds the Ollama-backed fallback that is invoked only when the deterministic rules are ambiguous or silent. `src/ollama_runtime.py` (at `src/`, not inside either package) is the (currently bypassed) startup-readiness helper for the Ollama server the LLM client talks to. Together these files implement a "cheap-first, LLM-as-tiebreaker" design: pattern matching handles the overwhelming majority of messages instantly and for free, and the LLM is reserved for the minority of cases where regex signals are weak, contradictory, or absent.

Back to [root README](../../README.md).

## 1. Design principle: rules first, LLM as fallback

Every entry point in this layer follows the same shape: try fast deterministic logic first, and only reach for `LLMClient.generate` (which makes a real HTTP call to a local Ollama server) when the deterministic signal is inconclusive. This shows up concretely in `src/nlu/initial_router.py::route_initial_decision`, in `src/nlu/language_detector.py::detect_language_with_fallback` (LLM only runs if `detect_language` returned `None`), and in every function of `src/llm/tasks.py`, each of which is gated behind an `enable_llm_polish` flag and returns an empty/`None`/"unknown" result immediately if that flag is off.

The reasoning, evident from the code:

- **Latency/cost**: an LLM call is a synchronous HTTP round-trip to a local Ollama process (`LLMClient.generate`, default `timeout_seconds=12.0` for polish calls, `LLM_TIMEOUT_SECONDS` env default `33` at the config layer) running a small model (`qwen3:0.6b` by default). A regex match is microseconds; an LLM call is hundreds of milliseconds to tens of seconds, and can fail (network error, timeout, malformed JSON).
- **Concurrency ceiling**: `main.py` wraps all LLM calls in a process-wide `threading.Semaphore` sized by `OLLAMA_MAX_CONCURRENCY` (default `1`), so only one Ollama inference can be in flight at a time across the whole process. If every message triggered an LLM call, throughput would collapse to one message at a time. Keeping the LLM as a fallback for only ambiguous messages keeps the common case (clear "book appointment" / "hi" / numeric menu replies) fast and unserialized.
- **Reliability**: regex classification is deterministic and testable; the LLM path is wrapped in defensive `try/except Exception: return {}` (or `None`/`"unknown"`) blocks everywhere in `src/llm/tasks.py`, because a small local model can return malformed JSON, wrong keys, or simply be unreachable. The system is designed to degrade to `"OTHER"` / `None` gracefully rather than crash.

## 2. `src/nlu/language_detector.py`

### `detect_language(lower)`

A pure heuristic, no LLM involved:

1. If the text contains any character in the Devanagari Unicode block (`ऀ-ॿ`), it is immediately classified `"hi"` — this catches any message actually typed in the Hindi script, regardless of vocabulary.
2. Otherwise it tokenizes the ASCII text into words (`[a-zA-Z]+`) and scores them against two fixed vocabularies:
   - `hi_tokens`: romanized Hindi words such as `mera`, `mujhe`, `karna`, `chahiye`, `nahi`, `haan`, `kal`, `aaj`.
   - `en_tokens`: English booking-domain words such as `book`, `appointment`, `available`, `confirm`, `doctor`, `tomorrow`.
3. Scoring logic: both scores > 0 → `"hinglish"` (mixed Hindi/English words in the same message); only English tokens matched → `"en"`; only Hindi-romanized tokens matched → `"hinglish"` (romanized Hindi alone is still treated as Hinglish, not pure Hindi — pure `"hi"` is reserved for actual Devanagari script); no tokens matched at all → `None` (undetermined).

### `detect_language_with_fallback(lower, llm_client, enable_llm_fallback)`

Calls `detect_language` first. Only if that returns `None` (i.e., the message had no Devanagari and no recognizable EN/HI tokens — e.g. a bare number, an emoji, or an out-of-vocabulary phrase) *and* `enable_llm_fallback` is true *and* an `llm_client` was supplied, does it escalate to `llm_detect_language` (`src/llm/tasks.py`), which is itself a thin wrapper around the same LLM classification prompt used for initial-message routing (see section 6). This is the one clear example in the codebase of the "escalate only when the cheap heuristic is silent" pattern applied specifically to language.

### `update_response_language(...)`

This function owns the policy for which language the bot replies in over the course of a conversation, and implements a "detect for the first couple of turns, then lock" policy:

1. **Explicit user override always wins**: if the message contains `"language english"` / `"speak english"`, `"language hindi"` / `"speak hindi"`, or `"language hinglish"` / `"speak hinglish"` / `"speak in hinglish"`, the response language is forced to `en`/`hi`/`hinglish` and marked `language_locked=True` immediately, bypassing everything else.
2. **`MIXED_RESPONSE_LANGUAGE` env override**: if that setting is one of `en`, `hi`, or `hinglish` (case-insensitively), the function short-circuits and always returns that fixed language, locked, regardless of what the user actually wrote. The default value read at the config layer (`src/config.py`) is `"auto"`, which does not match any of the three literal values, so by default this override is a no-op and normal per-turn detection runs. Setting `MIXED_RESPONSE_LANGUAGE=en` (for example) effectively pins every reply in every conversation to English no matter what language the user types in — a deployment-wide kill switch for language auto-detection.
3. **Once locked, stay locked**: if `language_locked` is already `True` (from a previous turn), the function just returns the existing `response_language` unchanged — no more detection work is done for the rest of the conversation.
4. **Otherwise, detect and count turns**: `language_turn_count` is incremented, `detect_language_with_fallback` is run on the current message, and if it returns a language, that becomes the new `response_language`. Once `language_turn_count >= 2` (i.e., after the second turn where detection actually ran) and the current `response_language` is one of the three known values, the language gets locked (`language_locked=True`) for the rest of the conversation.

The net effect: the bot lets the first one or two user messages determine the conversation's language dynamically (so it can pick up Hindi vs. Hinglish vs. English from how the user actually writes), then freezes that choice so it doesn't flip-flop mid-conversation if a later message happens to contain tokens from a different vocabulary (e.g., an English clinic name typed inside an otherwise Hindi sentence).

## 3. `src/nlu/extractors.py`

All functions here are pure, regex-based, and take already-lowercased text (`lower`) except where noted. They fall into two groups.

### Intent / signal classifiers (booleans or small enums, no LLM)

- **`is_booking_intent(lower)`** — the primary "user wants to book" detector. Matches English/Hinglish patterns (`\bbook\b`, `\bappointment\b`, `\bschedule\b.*\bappointment\b`, `mujhe...book`, etc.) and a parallel list of Devanagari patterns (`अपॉइंटमेंट`, `बुकिंग`, `चेकअप`, `डॉक्टर से मिलना`, ...).
- **`has_weak_booking_signal(lower)`** — a narrower, more ambiguous set of patterns (`doctor se milna`, `meet the doctor`, `doctor consult`) that *could* mean "I want to book" but could equally mean "I already have an appointment" or a general query about a doctor. This is the signal `initial_router.py` uses to decide whether a booking-looking message is actually confident enough to auto-route, or needs an LLM tiebreak. Note the Devanagari variants here are mis-encoded (mojibake `à¤¡à¥‰...` byte sequences rather than real Devanagari codepoints), which looks like a latent encoding bug — those particular Devanagari weak-signal patterns will not match correctly-encoded Hindi text.
- **`has_booking_negative_signal(lower)`** — patterns that indicate the message is *not* actually a booking request even if it superficially looks like one: `no appointment`, `don't book`, `without appointment`, `nahi`/`nah`, plus unrelated words that could false-positive on "book"-adjacent context like `house`, `home`, `interview`. This runs *before* the positive booking check in the router and, when it fires, forces an LLM decision rather than trusting regex alone.
- **`is_availability_intent(lower)`** — detects "is the doctor free / what slots are available" style queries (`availability`, `slot`, `free`, `khali`, `doctor available hai`) plus Devanagari equivalents (`उपलब्धता`, `स्लॉट`, `खाली`).
- **`is_greeting_intent(lower)`** — matches `hi|hello|hey|hii|namaste` and Hindi greetings (`नमस्ते`, `नमस्कार`, `हेलो`).
- **`is_restart_intent(lower)`** — exact-match set (`restart`, `reset`, `start over`, `new appointment`, `new booking`) — deliberately strict (`lower in {...}`, not a substring/regex search) to avoid accidentally restarting mid-conversation on a partial match.
- **`is_end_intent(lower)`** — matches a full-string `end`/`end now` or any of `end process`, `stop`, `cancel`, `quit`, `exit` appearing anywhere in the message, for ending the conversation.
- **`is_yes(lower)` / `is_no(lower)`** — confirmation-step yes/no detectors (`yes|y|confirm|confirmed|ok|done` vs. `no|n|nah|not now|change|edit|modify`); used when the bot is asking the user to confirm booking details.
- **`resolve_change_target(lower)`** — when the user says they want to change something during confirmation (caught by `is_no`), this maps free-text substrings (`"time"`, `"date"`/`"day"`, `"clinic"`/`"branch"`/`"location"`, `"phone"`/`"number"`/`"contact"`, `"name"`, `"appointment type"`/`"online"`/`"walkin"`) to a specific conversation-state target such as `ASK_TIME`, `ASK_DATE`, `ASK_CLINIC`, `ASK_PHONE`, `ASK_NAME`, `ASK_APPOINTMENT_MODE`, returning `None` if nothing matches (in which case the LLM-backed `llm_change_target` in `src/llm/tasks.py` can be used as a fallback by the caller).

### Entity extractors (return the parsed value or `None`)

- **`extract_name(text)`** — takes the raw (not necessarily lowercased) text, normalizes whitespace/punctuation, and rejects it outright if it's a bare greeting or contains any of a blocklist of domain words (`no`, `doctor`, `appointment`, `hospital`, `cancel`, `restart`, etc.) that indicate the message isn't actually a name. It then tries three regex patterns for explicit self-introduction (`my name is X`, `mera naam X`, `name: X`) before falling back to accepting the raw text as a name only if it's *purely* alphabetic words (`re.fullmatch(r"[a-zA-Z][a-zA-Z ]{1,48}", cleaned)`) — i.e., a bare reply like "Rahul Sharma" to a "what's your name?" prompt is accepted, but anything with digits or symbols is not.
- **`clean_name(name)`** — strips known intro phrases (`my name is`, `i am`, `this is`, `mera naam`, `mai`, `main`, etc., applied repeatedly until no more match) and title-cases the remaining words. Used both by `extract_name` and directly by callers that already isolated a name substring.
- **`extract_doctor_name(text)`** — looks for `dr.` / `doctor` followed by a capitalized-looking name span, then rejects the match if it actually captured an availability/scheduling word (`available`, `slot`, `today`, `tomorrow`, `date`, `time`) instead of a real name — i.e., it distinguishes "doctor available hai" (not a name) from "doctor Sharma" (a name).
- **`extract_phone(text)`** — strips all non-digit characters and requires *exactly* 10 digits; anything else (including valid-looking but longer/shorter numbers) returns `None`. This is stricter than the LLM's `phone` extraction task in `src/llm/tasks.py` (`llm_extract`), which accepts 10-15 digits.
- **`extract_date(text)`** — handles `today`/`tomorrow` literally (resolved against `now_in_runtime_timezone().date()` from `src/timezone_utils.py`, so it's timezone-aware), plus three explicit numeric formats (`YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`). Any parsed date earlier than today is rejected (returns `None`) — the extractor refuses to silently accept a past date.
- **`extract_time(text)`** — strips a leading `at`/`around`, then tries 12-hour `am`/`pm` format (with optional minutes), then 24-hour `HH:MM`, then a bare hour number (`"14"` → `"14:00"`), returning a normalized `HH:MM` 24-hour string or `None`.
- **`capture_prefill_entities(context, text)`** — a convenience orchestrator: if the given `context` object doesn't already have `appointment_date`/`appointment_time` set, it tries `extract_date`/`extract_time` on the text and fills them in via attribute assignment. Used to opportunistically prefill booking fields from an early free-text message (e.g. the very first message already contains "book for tomorrow at 5pm").

## 4. `src/nlu/initial_router.py` — `route_initial_decision`

This is the most important function in the module: it decides, from the very first user message, whether the conversation should go to `BOOK_APPOINTMENT`, `CHECK_AVAILABILITY`, `GREETING`, `GENERAL_QUERY`, `OTHER`, or `ABUSE`, plus which response language to use. It is a layered cascade — each layer only runs if the previous ones didn't already produce an answer — moving from free/instant checks to progressively more expensive ones, ending with an LLM call as the last resort.

Signature: `route_initial_decision(llm_client, enable_llm_polish, text, lower) -> (decision, language, abuse_flag)`.

Decision flow:

```
1. Baseline language guess
   detected_language = detect_language(lower)      # cheap heuristic, used as the
                                                     # default/fallback language for
                                                     # every branch below

2. Numeric menu shortcuts (no LLM, no regex-intent matching)
   normalized in {"1", "option 1", "book now", "booking"} or starts with "1 "
       -> return BOOK_APPOINTMENT
   normalized in {"2", "option 2", "check availability"} or starts with "2 "
       -> return CHECK_AVAILABILITY
   (covers users replying to a numbered menu the bot showed earlier)

3. Negative booking signal check (has_booking_negative_signal)
   If the message contains negating language ("no appointment", "don't book",
   "nahi", "without appointment", "house", "interview", ...):
       -> always escalate to the LLM (_llm_decision, min_confidence=0.80)
          because regex alone can't safely tell CHECK_AVAILABILITY / GREETING /
          GENERAL_QUERY / OTHER apart from a true booking refusal
       -> if LLM flags abuse: return ABUSE
       -> if LLM intent in {CHECK_AVAILABILITY, GREETING, GENERAL_QUERY, OTHER}:
              return that intent
       -> otherwise (LLM gave BOOK_APPOINTMENT or nothing usable):
              return GENERAL_QUERY  (deliberately never returns BOOK_APPOINTMENT
              here, since the message already showed a negative-booking cue)

4. Availability intent (is_availability_intent) — cheap, no LLM
       -> return CHECK_AVAILABILITY directly

5. Booking intent (is_booking_intent) — cheap, no LLM
   5a. If also a "weak" booking signal (has_weak_booking_signal, e.g.
       "doctor se milna" / "meet the doctor" — ambiguous phrasing):
           -> escalate to LLM (_llm_decision, min_confidence=0.80)
           -> abuse -> ABUSE
           -> otherwise use whatever intent the LLM returned, or GENERAL_QUERY
              if the LLM produced nothing usable
   5b. Otherwise (strong, unambiguous booking phrase):
           -> return BOOK_APPOINTMENT directly, no LLM call

6. Greeting intent (is_greeting_intent) — cheap, no LLM
       -> return GREETING directly

7. Final fallback: nothing matched any deterministic rule
       -> LLM classification as last resort (_llm_decision, min_confidence=0.70,
          a slightly lower bar than the escalation paths above since there is no
          regex signal at all to corroborate)
       -> abuse -> ABUSE
       -> otherwise return (LLM intent or "OTHER"), (LLM language or detected_language)
```

Two structural details worth calling out:

- **`_llm_decision` is a local closure** that wraps `llm_classify_initial_message` (from `src/llm/tasks.py`), defaulting to `min_confidence=0.80` for the two escalation paths (negative-signal and weak-booking-signal) and lowering it to `0.70` for the final catch-all — i.e., when regex found *no* signal at all, the router accepts a slightly less confident LLM answer rather than defaulting straight to `OTHER`.
- **Abuse detection is not a separate call** — it rides along inside the same `llm_classify_initial_message` JSON response (see section 6) as an `"abuse"` boolean. Every branch that calls `_llm_decision` checks `abuse` first and short-circuits to `ABUSE` before considering the classified intent at all, so an abusive message is always caught by any code path that reaches the LLM, regardless of which regex branch triggered the escalation.
- If `enable_llm_polish` is `False`, `llm_classify_initial_message` returns `{}` immediately (see section 6), so every `_llm_decision()` call degrades to `("OTHER", detected_language, False)` — the router still returns a coherent (if less precise) answer with the LLM fully disabled.

## 5. `src/llm/client.py` — `LLMClient`

`LLMClient` is a minimal, dependency-free (stdlib `urllib`) HTTP client for Ollama's `/api/chat` endpoint. Key points from `generate(system_prompt, user_prompt)`:

- **Ollama is the only implemented provider.** The constructor accepts a `provider` string (default `"ollama"`), but `generate` raises `RuntimeError(f"Unsupported LLM provider: {self.provider}")` immediately if `self.provider != "ollama"`. There is no branching logic for any other provider anywhere in this class — the `provider` parameter exists as a guard/future-extension point, not a real abstraction today.
- **Request shape**: POSTs to `{base_url}/api/chat` with a JSON body of `{"model": ..., "messages": [{"role": "system", ...}, {"role": "user", ...}], "stream": false, "options": {"temperature": 0.2}}`. Streaming is explicitly disabled (`"stream": False`) — the client always waits for the full response body rather than consuming a token stream, which simplifies the strict-JSON-parsing contract used throughout `src/llm/tasks.py` (there's no partial-JSON reassembly to do).
- **Temperature is fixed at `0.2`**, a low value favoring deterministic/consistent output — appropriate given every prompt in `tasks.py` demands a specific structured format (single JSON object or a single fixed token) rather than open-ended generation.
- **Timeout handling**: `urllib.request.urlopen(..., timeout=self.timeout_seconds)` (constructor default `12.0`); `HTTPError`, `URLError`, and `TimeoutError` are all caught and re-raised as a single `RuntimeError` with a descriptive message, so callers in `src/llm/tasks.py` only need to catch a generic `Exception` and treat any LLM failure uniformly as "no answer."
- **Empty-content guard**: if Ollama responds successfully but `data["message"]["content"]` is empty after stripping, `generate` raises `RuntimeError("LLM returned empty content")` rather than returning an empty string silently.

## 6. `src/llm/tasks.py` — LLM task functions

Every function here is a thin, single-purpose wrapper around `LLMClient.generate` with a fixed prompt and a strict output contract, parsed defensively so a malformed model response never propagates as an exception to the caller.

- **`llm_classify_initial_message(llm_client, enable_llm_polish, text, min_confidence)`** — the central classification call, used directly by `route_initial_decision`'s escalation paths and indirectly by `llm_route_intent_and_language`, `llm_detect_language`, and `llm_detect_abuse`. Prompt instructs the model (few-shot examples included, covering English, Devanagari, and Hinglish, plus one explicit abuse example) to return **strict JSON only**: `{"intent": "BOOK_APPOINTMENT|CHECK_AVAILABILITY|GREETING|GENERAL_QUERY|OTHER", "language": "EN|HI|HINGLISH|UNKNOWN", "abuse": "ABUSE|NONE", "confidence": 0.0}`. The response is parsed with `parse_first_json_object`, then each field is validated against an allow-list (`intent` must be one of the five known values or the whole result is discarded and `{}` is returned; `language` is normalized to lowercase `en`/`hi`/`hinglish` or `None`). Returns `{}` immediately if `enable_llm_polish` is `False`, and `{}` on any exception.
- **`llm_extract_booking_prefill(llm_client, enable_llm_polish, text)`** — asks the model to pull `patient_name`, `appointment_date`, `appointment_time`, `clinic_name`, `booking_for` (`self|other|unknown`) out of the very first message in one shot (used to prefill as much of the booking form as possible from an information-dense opening message like "book appointment for tomorrow 5pm for my father at City Clinic"). Each field is independently validated after parsing — dates must match `\d{4}-\d{2}-\d{2}`, times `\d{2}:\d{2}`, `booking_for` must be one of the three allowed values — and only valid fields are included in the returned dict, so a partially-correct LLM response still yields a partially-useful result rather than being discarded wholesale.
- **`llm_extract(llm_client, enable_llm_polish, field_name, text)`** — single-field extractor for `phone`, `date`, or `time` (generic version used later in the conversation, e.g. when the deterministic `extract_phone`/`extract_date`/`extract_time` in `extractors.py` come back empty for a given turn). Model is told to output just the value or the literal string `EMPTY`. Output is validated per field: phone must reduce to 10-15 digits, date must match `YYYY-MM-DD`, time must match `HH:MM`.
- **`llm_route_intent_and_language(llm_client, enable_llm_polish, text, min_confidence)`** — a convenience wrapper that just calls `llm_classify_initial_message` and returns the `(intent, language)` tuple, discarding `abuse`/`confidence`.
- **`llm_detect_language(llm_client, enable_llm_polish, text)`** — also just calls `llm_classify_initial_message` (with `min_confidence=0.60`, the lowest bar in the module, since here only the `language` field is actually consulted) and returns `parsed.get("language")`. This is what `detect_language_with_fallback` in `language_detector.py` calls when the regex heuristic can't determine a language.
- **`llm_detect_abuse(llm_client, enable_llm_polish, text)`** — same underlying call again (`min_confidence=0.70`), returning just the boolean `abuse` field. Note that `route_initial_decision` doesn't use this helper directly — it inlines the same check on the result of `_llm_decision`'s own `llm_classify_initial_message` call — but this function exists as a reusable standalone abuse check for other call sites.
- **`llm_detect_confirm_intent(llm_client, enable_llm_polish, text)`** — used at the confirmation step (yes/no/change a field) when `is_yes`/`is_no` in `extractors.py` don't confidently resolve it. Prompt asks for exactly one token: `YES`, `NO`, `CHANGE`, `UNKNOWN`; the response is matched by prefix (`out.startswith("YES")`, etc.) and mapped to lowercase `"yes"`/`"no"`/`"change"`/`"unknown"`. Not JSON — a single-token contract, simpler than the classification prompt.
- **`llm_change_target(llm_client, enable_llm_polish, text)`** — LLM fallback for `resolve_change_target` in `extractors.py` when the user's free-text description of what they want to change doesn't match any of the substring rules there. Prompt asks for exactly one of `ASK_NAME`, `ASK_PHONE`, `ASK_DATE`, `ASK_TIME`, `ASK_CLINIC`, `ASK_APPOINTMENT_MODE`, `UNKNOWN`; result validated against an allow-list, returning `None` for `UNKNOWN` or anything else unrecognized.
- **`parse_first_json_object(raw)`** — the shared defensive JSON parser used by `llm_classify_initial_message` and `llm_extract_booking_prefill`. First tries `json.loads` on the whole trimmed string; if that fails (e.g. the model wrapped the JSON in prose or markdown fences), it falls back to locating the first `{` and the last `}` in the string and attempting `json.loads` on that substring. Returns `None` if neither attempt yields a JSON object (a JSON array or scalar at top level is also rejected — `isinstance(parsed, dict)` is required). This is what makes the whole LLM layer tolerant of small local models that don't always emit clean, unwrapped JSON.

## 7. `src/ollama_runtime.py`

Defines `OllamaStartupError` (a plain `RuntimeError` subclass) and `ensure_ollama_ready(base_url, model, auto_start, auto_pull, timeout_seconds)`, intended to be called once at process startup to guarantee the Ollama server is up and the configured model is pulled before the app starts serving traffic:

1. `_is_ollama_up` pings `{base_url}/api/tags`; if that succeeds, `_ensure_model` checks whether the configured model is already present (`_model_exists`, also tolerant of an implicit `:latest` tag) and, if not and `auto_pull` is true, shells out to `ollama pull <model>` (`_pull_model`, `subprocess.run` with a 1800s timeout).
2. If Ollama isn't reachable and `auto_start` is false, raises `OllamaStartupError` telling the operator to set `OLLAMA_AUTO_START=true` or run `ollama serve` manually.
3. If `auto_start` is true, it launches `ollama serve` as a detached subprocess (`_start_ollama_process`) and polls `_is_ollama_up` every 0.5s until either it comes up (then proceeds to `_ensure_model`) or `timeout_seconds` elapses, at which point it raises `OllamaStartupError`.

**This check is currently disabled in the running application.** `main.py` imports `ensure_ollama_ready`/`OllamaStartupError` and has the call site present, but the actual `ensure_ollama_ready(...)` invocation is commented out with the note `"TEMPORARILY DISABLED - Ollama check causes startup to fail when Ollama not available"`, replaced by log lines stating validation was skipped and that Ollama must be started manually (`ollama serve` on the host). In other words: **Ollama readiness is not verified at boot in the current deployment** — the app assumes an Ollama server is already running externally at `OLLAMA_BASE_URL` with the configured model available, and if it isn't, the first LLM call will simply fail at request time (caught by the defensive `try/except` blocks in `src/llm/tasks.py`, degrading that turn to the non-LLM fallback) rather than the process refusing to start.

## 8. Concurrency: one Ollama call at a time

`main.py` creates a process-wide `threading.Semaphore(_ollama_max_concurrency)` where `_ollama_max_concurrency = max(1, int(os.getenv("OLLAMA_MAX_CONCURRENCY", "1")))`, and every LLM call made anywhere in the message-handling pipeline is expected to acquire this semaphore first. With the default value of `1`, this means **only one Ollama inference can be in flight across the entire process at any moment** — concurrent requests that all need the LLM will queue and wait their turn rather than hammering the local Ollama server in parallel. This reinforces why the deterministic-first design in `extractors.py`/`initial_router.py`/`language_detector.py` matters: since LLM calls are serialized process-wide, keeping the LLM off the hot path for the common case is what keeps overall message throughput acceptable. (The semaphore itself is defined and acquired in `main.py`, not in `src/nlu/` or `src/llm/` — this layer only defines the functions that get called while holding it.)

## 9. Environment variables

| Variable | Default (from `src/config.py`) | Effect |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Selects the LLM backend. `LLMClient.generate` only implements `"ollama"` and raises for anything else. |
| `LLM_MODEL_NAME` | `qwen3:0.6b` | Model name passed to Ollama's `/api/chat` and to `ensure_ollama_ready`'s pull/check logic. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Base URL for the Ollama HTTP API used by both `LLMClient` and `ollama_runtime.py`. |
| `LLM_TIMEOUT_SECONDS` | `33` | Request timeout (seconds) for `LLMClient.generate` calls (the `LLMClient` class's own constructor default is `12.0`, but the app wires this config value in). |
| `ENABLE_LLM_POLISH` | `true` | Master switch for the entire LLM layer. When false, every function in `src/llm/tasks.py` short-circuits to an empty/`None`/`"unknown"` result, and `route_initial_decision` falls back to pure regex classification (defaulting to `OTHER` whenever regex alone is inconclusive). |
| `OLLAMA_AUTO_START` | `true` | Whether `ensure_ollama_ready` should launch `ollama serve` itself if unreachable. Currently moot in practice since the call site in `main.py` is commented out. |
| `OLLAMA_AUTO_PULL` | `true` | Whether `ensure_ollama_ready`/`_ensure_model` should run `ollama pull <model>` automatically if the configured model isn't present. Also currently moot for the same reason. |
| `OLLAMA_STARTUP_TIMEOUT_SECONDS` | `30` | How long `ensure_ollama_ready` polls for Ollama to come up before raising `OllamaStartupError`. Currently moot (see above). |
| `OLLAMA_MAX_CONCURRENCY` | `1` | Size of the process-wide semaphore (defined in `main.py`) that serializes all LLM calls; see section 8. |
| `MIXED_RESPONSE_LANGUAGE` | `auto` | When set to `en`, `hi`, or `hinglish`, forces `update_response_language` to always return that language, locked, ignoring per-turn detection entirely. The default `auto` does not match any of those three values, so normal detect-then-lock behavior (section 2) applies. |

Back to [root README](../../README.md).
