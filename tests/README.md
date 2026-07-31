# Test Suite Guide

~140 files. This suite doubles as the closest thing this project has to a
written spec: most non-trivial behaviors (state transitions, concurrency
guards, multi-channel routing, localization, abuse handling) were locked
down by a dedicated test before or alongside implementation. When in doubt
about "is X actually implemented and safe to rely on," check for a test
file matching the feature name first.

Back to [root README](../README.md).

## Naming conventions

| Pattern | Meaning |
|---|---|
| `req_0xx_*.py` | Numbered "requirement contract" tests — one specific behavior guarantee each, run as part of the normal `pytest -q` suite. See table below. |
| `test_prod_pointN_*.py` | A numbered production-readiness checklist (points 1–7): overflow-queue persistence, notification retry/DLQ, delivery-status persistence, timeout-safe branch, multi-instance SQL locking, queue monitoring stats. Evidence the queue/notification infra is deliberately hardened for multi-instance production deployment, not just single-process happy-path code. |
| `test_hard_*.py` | Stress/edge-case tests: queue resilience under load, scheduler retry/dead-letter behavior. |
| `integration_*.py` | End-to-end scenarios against real or close-to-real data (e.g. a specific doctor/patient dataset) rather than mocked fixtures. |
| `live_*.py`, `demo_*.py`, `qwen17b_eval_*.py` | Manual/local-run scripts, not part of automated CI. Includes `live_whisper_browser_stream.py` (the standalone voice prototype — see [src/live_whisper/README.md](../src/live_whisper/README.md)), QR preview generators, and an LLM model-eval harness. |
| `_diag_*.py` | Ad hoc diagnostic scripts, not pytest test modules. |
| Everything else (`test_*.py`) | Standard automated pytest suites, grouped by feature area below. |

## `req_0xx_*` requirement contracts

| File | Behavior locked down |
|---|---|
| `req_001_fsm_state_transitions.py` | Core FSM state transition correctness |
| `req_002_prompt_consistency.py` | Prompt/message wording consistency |
| `req_003_antispam_one_response.py` | De-dup / one-response-per-inbound-message guarantee |
| `req_004_crash_auto_recovery.py` | Session recovery after a crash |
| `req_005_known_unknown_patient.py` | Known vs. unknown patient branching |
| `req_006_doctor_excel_list.py` | Doctor-list Excel export (reminder reports) |
| `req_007_slot_snap_t10.py` | T-10-minute slot snapping behavior |
| `req_008_go_back_press_0.py` | "Press 0 to go back" navigation |
| `req_009_check_availability_flow.py` | Availability-check flow (distinct from booking) |
| `req_010_redis_user_processing_guard.py` | Redis-backed per-user processing lock |
| `req_011_redis_burst_concurrency_guard.py` | Burst-of-messages concurrency guard |
| `req_012_duplicate_telegram_chat_id_recovery.py` | Telegram chat-id dedup/recovery |
| `req_013_hard_patient_upsert_scenarios.py` | Patient upsert edge cases |
| `req_014_hard_option_text_normalization.py` | Numeric/text menu-option normalization |
| `req_015_redis_session_snapshot.py` | Redis session snapshotting |
| `req_016_processing_guard_non_blocking_fallback.py` | Non-blocking fallback when the guard is busy |
| `req_017_doctor_cache_accepted_days_fallback.py` | Doctor accepted-days cache fallback |
| `req_018_doctor_cache_invalidation_logic.py` | Cache invalidation queue logic |
| `req_019_appointment_incremental_cache_update.py` | Incremental cache update on booking |
| `req_020_init_abuse_llm_skip.py` | Abuse detection skipping the LLM call when rules already decide |
| `req_021_doctor_extra_contacts_real.py` | Extra doctor WhatsApp/Telegram contacts for reminders |
| `req_022_doctor_leaves_blocks_availability.py` | Doctor leave days blocking availability |
| `req_024_go_back_all_states.py` | Go-back navigation from every state |

(`req_023` is absent — treat it as removed/renumbered, not a gap to fill.)

## Feature-area groupings (`test_*.py`)

- **Booking/FSM flow**: `test_availability_*` (flow, latency, redis-snapshot-primary, live display, snapshot-flow-back), `test_existing_booking_*`, `test_known_patient_multiturm.py`, `test_known_telegram_patient_phone_autofill.py`, `test_patient_flow_sanjay.py`, `test_patient_known_and_abuse_flow.py`, `test_session_state_isolation.py`, `test_session_snapshot_real.py`, `test_completed_cancelled_loopholes.py`, `test_booking_session_window_numbering.py`.
- **Language/localization**: `test_hindi_*` (4 files), `test_date_prompt_localization.py`, `test_time_period_prompt_localization.py`, `test_language_selection_flow.py`.
- **Abuse/init routing**: `test_abuse_escalation_policy.py`, `test_init_*` (ambiguity fallback, clarify greeting, prefill LLM routing, single LLM fallback), `test_initial_stage.py`.
- **Multi-channel routing**: `test_stage2_multi_account_routing_suite.py`, `test_stage2_dynamic_real_user_flows.py`, `test_telegram_keyed_webhook_routing.py`, `test_telegram_known_patient.py`, `test_telegram_patient_id_formatting.py`, `test_channel_delivery_account_credentials.py`.
- **Channel-specific webhooks**: `test_infobip_webhook_smoke.py`, `test_meta_env_webhook_smoke.py`, `test_evolution_webhook_routes.py`, `test_evolution_policy.py`, `test_evolution_temp_autoresponse_policy.py`, `test_sms_notification_routing.py`, `test_webhook_ack_light.py`.
- **QR/web widget**: `test_qr_checkin_route.py`, `test_qr_checkin_service.py`, `test_qr_generate.py`, `test_qr_generator_service.py`, `test_qr_overflow_notification_bugfix.py`, `test_google_drive_qr_asset.py`, `test_whatsapp_web_lookup_same_day.py`.
- **Repository/DB correctness**: `test_booking_repository_modular_wiring.py`, `test_repo_bugfixes_individual.py`, `test_doctor_accept_days_redis_primary.py`, `test_redis_today_slots_and_patient_cache.py`.
- **Queue/runtime infra**: `test_runtime_queue_and_dedup.py`, `test_turn_queue_timeout_guard.py`, `test_user_turn_buffer.py`, `test_queue_overflow_four_patients.py`, `test_overflow_kafka_handoff.py`, `test_kafka_notification_bridge.py`, `test_kafka_turn_bridge.py`.
- **Voice (opt-in/manual only)**: `test_live_whisper_mic_cpu.py`, `test_live_whisper_parchi_noise.py` — gated behind `RUN_LIVE_WHISPER_MIC_TEST=1`, not run by default.
- **Broad/behavioral**: `test_behaviour_contract.py`, `test_comprehensive_requirements.py`, `test_production_runtime_flow.py`, `test_recent_three_changes.py`, `test_recent_ux_changes.py`, `test_timing_diagnostic.py`.

## Non-test files in this directory

`conftest.py` (pytest fixtures), `_diag_db.py` / `_diag_reminder.py`
(diagnostic scripts), `avail_out.txt` / `avail_test_output.txt` (captured
output, not source), `Evolution_api_commands.txt` (a curl cheat-sheet),
`qr-aman-clinicone-4-11.svg` / `qr_generate_preview.html` (QR preview
artifacts), and `live_whisper_browser_stream.py` (the standalone voice app
documented in [src/live_whisper/README.md](../src/live_whisper/README.md)).
None of these run as part of `pytest -q`.

## Running the suite

Everything:
```bash
pytest -q
```

Just the core FSM contracts:
```bash
pytest -q tests/test_fsm_flow_individual.py tests/req_001_fsm_state_transitions.py tests/req_005_known_unknown_patient.py tests/req_008_go_back_press_0.py tests/req_009_check_availability_flow.py
```

Manual/opt-in tests (voice, live LLM eval, demos) are excluded by default
and must be run explicitly by filename, with any required env vars
(`RUN_LIVE_WHISPER_MIC_TEST=1`, etc.) set first.
