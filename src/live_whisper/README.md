# Live Whisper — Experimental Voice Prototype

**This is not part of the production bot.** It is a separate, standalone
FastAPI application for live-microphone speech-to-text transcription of
doctor prescription dictation ("parchi"), with an Ollama LLM cleanup/
structuring pass on top. Nothing under `main.py` imports it, it runs on a
different port, and it has its own startup scripts. Read this before
assuming it's reachable through the deployed bot.

Back to [root README](../../README.md).

## Why it lives here

`src/live_whisper/` currently holds one file
(`load_medicine_data.py`) that is only a supporting data-import utility.
The actual prototype application lives outside `src/`, at
`tests/live_whisper_browser_stream.py` — put there because it began life as
a manual verification script, not because it's a test in the pytest sense.
This README documents both pieces together since they form one feature.

## The prototype app: `tests/live_whisper_browser_stream.py`

A ~3,500-line standalone `FastAPI` app (`app = FastAPI(title="Live Whisper
Browser Stream Test")`) that implements: browser microphone → chunked audio
upload → `faster-whisper` transcription → optional Ollama LLM cleanup /
structuring → a rendered "parchi" (prescription note) image.

It loads `LLMClient` from `src/llm/client.py` via a manual `importlib` spec
load (`_load_test_llm_client()`, line 23) instead of a normal package
import — a deliberate way to reuse the production LLM client without
coupling this prototype into the main app's import graph.

### Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves the HTML/JS recording UI (large inline page). |
| GET | `/favicon.ico` | Empty 204 response. |
| GET | `/parchi-template.png` | Serves `Doctor_parchi.png` as the prescription template background. |
| POST | `/transcribe-chunk` | Accepts a multipart audio chunk (`session_id` + `audio_chunk` file), runs VAD/quality filtering, transcribes via faster-whisper, appends accepted text to the session transcript. |
| POST | `/clean-transcript` | Sends the accumulated raw transcript to the Ollama LLM to clean it into readable prescription text; caches per-session so identical input isn't re-cleaned. |
| POST | `/clean-edit-field` | Cleans one edited field's value (e.g. after a doctor manually corrects a field) via the LLM. |
| POST | `/render-parchi` | Extracts structured fields from the cleaned text via the LLM and renders the final "parchi" payload; falls back to `_fallback_parchi_payload` if the LLM call times out or errors. |
| POST | `/reset` | Clears all per-session in-memory state for a given `session_id`. |

### Audio pipeline (`/transcribe-chunk`)

Each chunk goes through several accept/reject gates before it's kept:

1. **`_prepare_wav_for_transcription`** — converts/validates the uploaded
   chunk; rejects it early (`skipped: true`) if it fails.
2. **Whisper transcription** — runs in a thread pool
   (`run_in_threadpool(_transcribe_chunk_blocking, ...)`) with a hard
   timeout (`WHISPER_REQUEST_TIMEOUT_SECONDS`, default 60s), using a
   rolling `prompt_tail` (last `WHISPER_PROMPT_TAIL_CHARS` chars of prior
   transcript) so the model has short-term context across chunks.
3. **`_is_low_value_transcript`** — drops chunks whose transcript is noise
   (e.g. below `MIN_ACCEPTED_WORDS`, or fails RMS/speech-ratio checks
   against `MIN_CHUNK_RMS` / `MIN_SPEECH_RATIO`).
4. **`_is_relevant_transcript`** — checks the text against
   `MEDICAL_KEYWORDS` (a large set seeded from generic prescription
   vocabulary plus every word extracted from `COMMON_MEDICINE_TERMS`) to
   filter out transcribed chatter unrelated to the consultation.
5. **`_is_duplicate_transcript`** — uses `SequenceMatcher` similarity
   (`WHISPER_DEDUPE_SIMILARITY`, default 0.98) against the previous chunk to
   drop near-identical repeats (common with overlapping audio chunks).

Accepted chunks are saved to disk under
`WHISPER_SAVE_AUDIO_DIR` (default `tests/artifacts/live_whisper_audio`),
appended to an in-memory per-session transcript list, and used to update a
per-session "medicine vocabulary" set that biases future transcription
prompts toward drug names already mentioned in this session.

### Medicine-name vocabulary biasing

`COMMON_MEDICINE_TERMS` is a hardcoded list of ~50 common Indian
prescription drug names/brands (Dolo, Crocin, Pantop, Azithral, etc.),
folded into `DEFAULT_MEDICAL_PROMPT` — a Whisper "initial prompt" string
that biases transcription toward correctly recognizing these terms, timing
words (OD/BD/TDS/QID), and vitals vocabulary. The prompt explicitly tells
the model these are "vocabulary hints only; do not add a medicine unless it
is spoken," to reduce hallucinated drug names.

### Key environment variables

`WHISPER_MODEL` (default `large-v3-turbo`), `WHISPER_LANGUAGE` (default
`en`), `WHISPER_DEVICE` (`auto`/`cuda`/`cpu`), `WHISPER_COMPUTE_TYPE`,
`LLM_MODEL_NAME` (default `qwen3:1.7b` for this app — a larger model than
the main bot's default `qwen3:0.6b`), `OLLAMA_BASE_URL`,
`WHISPER_USE_LLM_CLEANER`, `WHISPER_USE_LLM_STRUCTURER`,
`WHISPER_CHUNK_SECONDS` (default 8s), `WHISPER_SILENCE_SECONDS`,
`WHISPER_VAD_THRESHOLD` and related VAD tuning vars, `WHISPER_SAVE_AUDIO_DIR`.

### Running it

Not started by `main.py` / `uvicorn main:app`. Instead:

```bash
uvicorn tests.live_whisper_browser_stream:app --host 127.0.0.1 --port 8010
```

or, on a GPU host, via `scripts/start_live_whisper_gpu.sh`, which:
- assumes a project layout at `/root/MessageBot` with a `.venv310` virtualenv,
- creates NVIDIA device nodes (`/dev/nvidiactl`, `/dev/nvidia0`, etc.) if missing,
- sets `WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16`,
- adds pip-installed NVIDIA CUDA runtime libs to `LD_LIBRARY_PATH`,
- runs `uvicorn tests.live_whisper_browser_stream:app` on port 8010.

`scripts/start_ollama_gpu_host.sh` is a separate launcher for a
GPU-accelerated `ollama serve` (`OLLAMA_LLM_LIBRARY=cuda_v13`), meant to be
started alongside this app on the same host so both the whisper prototype
and (optionally) the main bot share one GPU-backed Ollama instance.

### Manual/opt-in tests

`tests/test_live_whisper_mic_cpu.py` and
`tests/test_live_whisper_parchi_noise.py` use real microphone input and
`openai-whisper`; they are gated behind `RUN_LIVE_WHISPER_MIC_TEST=1` and
are not part of the default `pytest -q` run.

## `load_medicine_data.py` — offline medicine-name importer

Despite living in the `live_whisper` package, this file has nothing to do
with audio — it's a standalone CLI that seeds a **separate** MySQL table
(`medicine_master`) with drug-name/strength/form data, presumably to give
the whisper prototype (or a future feature) a canonical drug dictionary to
validate/autocomplete against. It is not imported by `main.py`, the FSM, or
any other production code path.

- **Separate database**: reads `DATABASE_URL_medicine` (must start with
  `mysql+mysqlconnector://`) — a distinct connection from the main bot's
  `DATABASE_URL`.
- **Sources it can pull from** (`--source-api`): `openfda`, `dailymed`,
  `rxnorm`, `pmbi` (PMBI India), `india-github` (a public Indian medicine
  dataset CSV on GitHub), `india-hybrid`, `indian-common`. Or `--source-file`
  to load a local `.csv`/`.tsv`/`.json`/`.jsonl` file.
- **Filtering**: excludes non-medicine PMBI catalog groups (e.g. "Surgical &
  Medical Consumables") and filters out consumable-sounding items (catheter,
  syringe, gauze, glove, etc.) via `PMBI_NON_MEDICINE_KEYWORDS`.
- **Normalization**: `FORM_ALIASES` collapses dosage-form variants (e.g.
  "tablets" → "tablet", "eye drops"/"ear drops"/"nasal drops" → "drops").
- **CLI flags**: `--source-file`, `--source-api`, `--source-name` (label
  stored per row, default `manual-import`), `--delimiter`, `--dry-run`,
  `--limit`/`--batch-size` (API paging), `--purge-source` (delete rows for a
  source label), `--reset-table` (truncate `medicine_master`),
  `--replace-source` (delete-then-reimport for one source).

Example:
```bash
python -m src.live_whisper.load_medicine_data --source-api india-github --source-name india-github --limit 5000
```

## Summary: what's real vs. what's wired in

| Aspect | Status |
|---|---|
| Audio capture, VAD filtering, Whisper transcription | Working prototype |
| LLM cleanup/structuring via Ollama | Working prototype |
| Medicine-vocabulary biasing | Working prototype |
| Medicine master-data import CLI | Working, but standalone/offline |
| Reachable from the deployed bot (`main.py`) | **No** |
| Covered by default `pytest -q` run | **No** (mic tests are opt-in only) |
