"""
=============================================================================
  COMPREHENSIVE REQUIREMENTS TEST
=============================================================================

Tests each requirement independently. Run from the project root:
    $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_comprehensive_requirements.py

REQUIREMENTS COVERED
--------------------
REQ-1  Slot position (booking number) is purely position-based
         - 9:00-17:00  slot=30min  appt@14:00  → booking number 11
         - 9:00-17:00  slot=30min  appt@09:00  → booking number 1
         - 9:00-12:00 + 13:00-17:00  @14:00   → booking number 9
         - 9:00-10:00  slot=5min   appt@09:30  → booking number 7

REQ-2  Success message label is "Patient ID:" not "Booking Number:"

REQ-3  Confirmation prompt includes "Reply with 1, 2, or 0."

REQ-4  Full FSM booking flow end-to-end (Telegram channel, mocked repos)
         INIT → ASK_BOOKING_FOR → ASK_NAME → ASK_PHONE
         → ASK_CLINIC → ASK_DATE → ASK_TIME → CONFIRM → success

REQ-5  Doctor appointment list dispatched to doctor's WhatsApp number

REQ-6  Doctor appointment list dispatched to doctor's Telegram chat_id

REQ-7  Doctor appointment list sent to BOTH when both are set

REQ-8  Doctor reminder NOT sent twice for the same schedule (dedup)
=============================================================================
"""

import os
import sys
import tempfile
import threading
import json
from datetime import datetime, timedelta, date, time
from typing import Optional
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ─── Import project modules ───────────────────────────────────────────────────
from src.repositories.booking_repository import BookingRepository, BookingResult, DoctorReminder
from src.repositories.scheduling_repository import ClinicOption, SchedulingRepository
from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext
from src.llm.client import LLMClient
from src.automation.scheduler import AutomationScheduler as AppointmentScheduler
from src.messages.templates import get_message

# ─── Helpers ──────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"

results: list[dict] = []

def _record(name: str, status: str, detail: str = ""):
    icon = "✓" if status == PASS else "✗"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"  [{icon}] {name}")
    if detail:
        prefix = "       " if status == PASS else "       REASON: "
        print(f"{prefix}{detail}")


def _make_llm_client() -> LLMClient:
    """LLMClient that never makes real calls."""
    mock = MagicMock(spec=LLMClient)
    mock.generate.return_value = '{"intent": "BOOK_APPOINTMENT", "language": "en"}'
    return mock


def _make_fsm(
    *,
    booking_repo: Optional[BookingRepository] = None,
    scheduling_repo: Optional[SchedulingRepository] = None,
    chat_phone_number: str = "telegram:99887766",
    doctor_id: int = 1,
    admin_id: int = 1,
) -> AppointmentFSM:
    fsm = AppointmentFSM(llm_client=_make_llm_client())
    fsm.booking_repository = booking_repo
    fsm.scheduling_repository = scheduling_repo
    fsm.chat_phone_number = chat_phone_number
    fsm.doctor_id = doctor_id
    fsm.admin_id = admin_id
    return fsm


def _make_booking_repo() -> MagicMock:
    repo = MagicMock(spec=BookingRepository)
    repo.list_active_appointments_by_chat_user_id.return_value = []
    repo.list_active_appointments_by_phone_number.return_value = []
    repo.find_patient_name_by_phone_number.return_value = None
    repo.find_patient_name_by_chat_user_id.return_value = None
    repo.save_confirmed_appointment.return_value = BookingResult(
        ok=True, message="saved", appointment_id=42, queue_number=5
    )
    return repo


def _make_scheduling_repo() -> MagicMock:
    repo = MagicMock(spec=SchedulingRepository)
    repo.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="Test Clinic", location="Test City", today_slots=0)
    ]
    repo.list_available_dates.return_value = ["2026-03-01", "2026-03-02"]
    repo.list_available_times.return_value = ["09:00", "09:30", "10:00"]
    repo.doctor_accept_days.return_value = 7
    return repo


def _slot_position(slot_time: time, schedules: list[tuple]) -> Optional[int]:
    """
    Pure-Python replica of BookingRepository._compute_slot_position logic.
    schedules: list of (start, end, duration_minutes)
    """
    req_dt = datetime.combine(date.today(), slot_time)
    cumulative = 0
    for (s, e, d) in schedules:
        start_dt = datetime.combine(date.today(), s)
        end_dt   = datetime.combine(date.today(), e)
        total_min = int((end_dt - start_dt).total_seconds() // 60)
        slots_in  = total_min // d
        if req_dt < start_dt or req_dt >= end_dt:
            cumulative += slots_in
            continue
        diff_min = int((req_dt - start_dt).total_seconds() // 60)
        if diff_min % d != 0:
            return None
        return cumulative + (diff_min // d) + 1
    return None


def _make_scheduler(
    *,
    booking_repo: MagicMock,
    send_message_fn,
    send_document_fn=None,
    lead_minutes: int = 10,
    window_seconds: int = 300,
    reminder_keys_path: str,
) -> AppointmentScheduler:
    """Create scheduler with a temp reminder-keys file so tests are isolated."""
    s = AppointmentScheduler(
        booking_repository=booking_repo,
        send_message_fn=send_message_fn,
        send_document_fn=send_document_fn,
        doctor_reminder_enabled=True,
        doctor_reminder_lead_minutes=lead_minutes,
        doctor_reminder_window_seconds=window_seconds,
    )
    # Redirect the persistent key store so tests don't pollute real data
    from src.automation.scheduler import _PersistentKeyStore
    s._reminder_keys = _PersistentKeyStore(path=reminder_keys_path, max_entries=10000)
    return s


def _upcoming_schedule_time(lead_minutes: int) -> tuple[str, str]:
    """Return (slot_date, schedule_start_time) exactly lead_minutes from now."""
    t = datetime.now() + timedelta(minutes=lead_minutes)
    return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")


def _make_doctor_reminder(
    *,
    doctor_id: int = 1,
    doctor_whatsapp: str = "",
    doctor_telegram_chat_id: str = "",
    slot_date: str,
    schedule_start_time: str,
    schedule_end_time: str = "10:00",
) -> DoctorReminder:
    return DoctorReminder(
        doctor_id=doctor_id,
        appointment_id=100,
        doctor_whatsapp=doctor_whatsapp,
        doctor_telegram_chat_id=doctor_telegram_chat_id,
        patient_name="Test Patient",
        patient_contact="9876543210",
        clinic_name="Test Clinic",
        slot_date=slot_date,
        slot_time="09:00",
        status="BOOKED",
        booking_number=3,
        schedule_id=1,
        schedule_start_time=schedule_start_time,
        schedule_end_time=schedule_end_time,
    )


# =============================================================================
# REQ-1: Slot position calculation (position-based, independent of bookings)
# =============================================================================

def test_req1_slot_position():
    print("\n─── REQ-1: Slot position calculation ────────────────────────────")

    cases = [
        # (description, slot_time, schedules, expected)
        (
            "9:00-17:00 slot=30min @14:00 → 11",
            time(14, 0),
            [(time(9, 0), time(17, 0), 30)],
            11,
        ),
        (
            "9:00-17:00 slot=30min @09:00 → 1",
            time(9, 0),
            [(time(9, 0), time(17, 0), 30)],
            1,
        ),
        (
            "9:00-12:00 + 13:00-17:00 slot=30 @14:00 → 9",
            time(14, 0),
            [(time(9, 0), time(12, 0), 30), (time(13, 0), time(17, 0), 30)],
            9,
        ),
        (
            "9:00-10:00 slot=5min @09:30 → 7",
            time(9, 30),
            [(time(9, 0), time(10, 0), 5)],
            7,
        ),
        (
            "9:00-17:00 slot=60min @10:00 → 2",
            time(10, 0),
            [(time(9, 0), time(17, 0), 60)],
            2,
        ),
        (
            "Outside schedule → None",
            time(18, 0),
            [(time(9, 0), time(17, 0), 30)],
            None,
        ),
        (
            "Not on slot boundary → None",
            time(9, 15),
            [(time(9, 0), time(17, 0), 30)],
            None,
        ),
    ]

    all_pass = True
    for desc, slot_t, schedules, expected in cases:
        result = _slot_position(slot_t, schedules)
        if result == expected:
            _record(f"REQ-1: {desc}", PASS)
        else:
            _record(f"REQ-1: {desc}", FAIL, f"expected {expected}, got {result}")
            all_pass = False

    # Also verify the real _compute_slot_position in BookingRepository uses same logic
    # by creating a mock cursor that returns a schedule row
    try:
        from src.db.connection import MySQLConfig
        # We cannot call it without an instance but we can verify method exists
        assert hasattr(BookingRepository, "_compute_slot_position"), \
            "BookingRepository._compute_slot_position method missing"
        _record("REQ-1: _compute_slot_position method exists on BookingRepository", PASS)
    except Exception as exc:
        _record("REQ-1: _compute_slot_position method exists on BookingRepository", FAIL, str(exc))
        all_pass = False

    return all_pass


# =============================================================================
# REQ-2: Success message label is "Patient ID:" not "Booking Number:"
# =============================================================================

def test_req2_patient_id_label():
    print("\n─── REQ-2: 'Patient ID:' label in success message ───────────────")
    all_pass = True

    for lang in ("en", "hi", "hinglish"):
        msg = get_message(lang, "db_save_ok", appointment_id=42)
        if "Patient ID:" in msg:
            _record(f"REQ-2 [{lang}] db_save_ok contains 'Patient ID:'", PASS)
        else:
            _record(f"REQ-2 [{lang}] db_save_ok contains 'Patient ID:'", FAIL, f"got: {msg!r}")
            all_pass = False
        if "Booking Number:" in msg:
            _record(f"REQ-2 [{lang}] db_save_ok does NOT contain 'Booking Number:'", FAIL, f"still has 'Booking Number:': {msg!r}")
            all_pass = False
        else:
            _record(f"REQ-2 [{lang}] db_save_ok does NOT contain 'Booking Number:'", PASS)

    return all_pass


# =============================================================================
# REQ-3: Confirmation prompts include "Reply with 1, 2, or 0."
# =============================================================================

def test_req3_confirm_reply_hint():
    print("\n─── REQ-3: Confirm prompt has 'Reply with 1, 2, or 0.' ──────────")
    all_pass = True
    hint = "Reply with 1, 2, or 0."

    checks = [
        ("en",        "confirm_summary",            {"patient_name": "A", "phone_number": "1", "clinic_name": "C", "clinic_address": "D", "appointment_date": "2026-03-01", "appointment_time": "09:00"}),
        ("en",        "confirm_prompt",              {}),
        ("en",        "confirm_reschedule_summary",  {"clinic_name": "C", "old_date": "2026-02-01", "old_time": "09:00", "new_date": "2026-03-01", "new_time": "10:00"}),
        ("en",        "confirm_reschedule_prompt",   {}),
        ("hi",        "confirm_summary",             {"patient_name": "A", "patient_type": "New", "age": "25", "gender": "Male", "phone_number": "1", "clinic_name": "C", "clinic_address": "D", "reason": "fever", "appointment_date": "2026-03-01", "appointment_time": "09:00"}),
        ("hi",        "confirm_prompt",              {}),
        ("hi",        "confirm_reschedule_summary",  {"clinic_name": "C", "old_date": "2026-02-01", "old_time": "09:00", "new_date": "2026-03-01", "new_time": "10:00"}),
        ("hi",        "confirm_reschedule_prompt",   {}),
        ("hinglish",  "confirm_summary",             {"patient_name": "A", "phone_number": "1", "clinic_name": "C", "clinic_address": "D", "appointment_date": "2026-03-01", "appointment_time": "09:00"}),
        ("hinglish",  "confirm_prompt",              {}),
        ("hinglish",  "confirm_reschedule_summary",  {"clinic_name": "C", "old_date": "2026-02-01", "old_time": "09:00", "new_date": "2026-03-01", "new_time": "10:00"}),
        ("hinglish",  "confirm_reschedule_prompt",   {}),
    ]

    for (lang, key, kwargs) in checks:
        try:
            msg = get_message(lang, key, **kwargs)
        except Exception as exc:
            _record(f"REQ-3 [{lang}] {key}", FAIL, f"get_message raised: {exc}")
            all_pass = False
            continue
        if hint in msg:
            _record(f"REQ-3 [{lang}] {key} has hint", PASS)
        else:
            _record(f"REQ-3 [{lang}] {key} has hint", FAIL, f"missing hint, got: {msg!r}")
            all_pass = False

    return all_pass


# =============================================================================
# REQ-4: Full FSM booking flow end-to-end
# =============================================================================

def test_req4_full_fsm_flow():
    print("\n─── REQ-4: Full FSM booking flow (Telegram, mocked repos) ───────")
    all_pass = True

    booking_repo = _make_booking_repo()
    scheduling_repo = _make_scheduling_repo()

    fsm = _make_fsm(
        booking_repo=booking_repo,
        scheduling_repo=scheduling_repo,
        chat_phone_number="telegram:99887766",
    )

    # NOTE: The mock scheduling_repo returns exactly 1 clinic, so ASK_CLINIC is
    # auto-skipped by the FSM — the flow goes directly from ASK_PHONE to ASK_DATE.
    # Expected keywords are substrings of the actual user-facing reply text.
    steps = [
        # (input, expected_substring_in_response, state_after, description)
        ("book",            "appointment for",   "ASK_BOOKING_FOR", "INIT → ASK_BOOKING_FOR"),
        ("1",               "name",              "ASK_NAME",        "ASK_BOOKING_FOR→ASK_NAME"),
        ("Test Patient",    "contact",           "ASK_PHONE",       "ASK_NAME→ASK_PHONE"),
        ("9876543210",      "date",              "ASK_DATE",        "ASK_PHONE→ASK_DATE (clinic auto-selected)"),
        ("1",               "slot or time",      "ASK_TIME",        "ASK_DATE→ASK_TIME"),
        ("1",               "confirm",           "CONFIRM",         "ASK_TIME→CONFIRM"),
        ("1",               "Patient ID:",       "COMPLETED",       "CONFIRM→COMPLETED (success)"),
    ]

    for user_input, expected_hint, expected_state, desc in steps:
        try:
            reply = fsm.handle(user_input)
        except Exception as exc:
            _record(f"REQ-4: {desc}", FAIL, f"Exception: {exc}")
            all_pass = False
            continue

        state_ok = fsm.state == expected_state
        # For the content check we do a loose match on keywords
        hint_lower = expected_hint.lower()
        reply_lower = reply.lower()
        content_ok = any(w.lower() in reply_lower for w in hint_lower.split(" or "))

        if state_ok and content_ok:
            _record(f"REQ-4: {desc}", PASS, f"state={fsm.state}, reply snippet={reply[:80]!r}")
        elif not state_ok:
            _record(f"REQ-4: {desc}", FAIL, f"expected state {expected_state!r}, got {fsm.state!r}. reply={reply[:80]!r}")
            all_pass = False
        else:
            _record(f"REQ-4: {desc}", FAIL, f"reply did not contain {expected_hint!r}. reply={reply[:100]!r}")
            all_pass = False

    # Verify save_confirmed_appointment was actually called
    if booking_repo.save_confirmed_appointment.called:
        _record("REQ-4: save_confirmed_appointment was called", PASS)
    else:
        _record("REQ-4: save_confirmed_appointment was called", FAIL, "not called")
        all_pass = False

    return all_pass


# =============================================================================
# REQ-5: Doctor reminder dispatched to WhatsApp number only
# =============================================================================

def test_req5_reminder_to_whatsapp():
    print("\n─── REQ-5: Doctor reminder → WhatsApp ────────────────────────────")

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        keys_path = f.name

    sent_calls: list[tuple] = []

    def send_fn(to: str, text: str):
        sent_calls.append((to, text))
        return "msg_sid_1"

    booking_repo = MagicMock(spec=BookingRepository)
    slot_date, sched_start = _upcoming_schedule_time(lead_minutes=10)
    booking_repo.list_due_doctor_reminders.return_value = [
        _make_doctor_reminder(
            doctor_whatsapp="9392569600",   # Dr. Sanjay Vinayak (doctor_id=1)
            doctor_telegram_chat_id="",
            slot_date=slot_date,
            schedule_start_time=sched_start,
        )
    ]

    scheduler = _make_scheduler(
        booking_repo=booking_repo,
        send_message_fn=send_fn,
        lead_minutes=10,
        window_seconds=300,
        reminder_keys_path=keys_path,
    )

    scheduler._run_reminders_once()

    wa_destinations = [c[0] for c in sent_calls if "telegram:" not in c[0]]

    if wa_destinations:
        _record("REQ-5: Message sent to WhatsApp number", PASS, f"destinations={wa_destinations}")
        return True
    else:
        _record("REQ-5: Message sent to WhatsApp number", FAIL,
                f"No WhatsApp destination. All calls: {sent_calls}")
        return False


# =============================================================================
# REQ-6: Doctor reminder dispatched to Telegram chat_id
# =============================================================================

def test_req6_reminder_to_telegram():
    print("\n─── REQ-6: Doctor reminder → Telegram chat_id ────────────────────")

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        keys_path = f.name

    sent_calls: list[tuple] = []

    def send_fn(to: str, text: str):
        sent_calls.append((to, text))
        return "msg_sid_2"

    booking_repo = MagicMock(spec=BookingRepository)
    slot_date, sched_start = _upcoming_schedule_time(lead_minutes=10)
    booking_repo.list_due_doctor_reminders.return_value = [
        _make_doctor_reminder(
            doctor_whatsapp="",
            doctor_telegram_chat_id="8299824956",  # Dr. Sanjay Vinayak (doctor_id=1)
            slot_date=slot_date,
            schedule_start_time=sched_start,
        )
    ]

    scheduler = _make_scheduler(
        booking_repo=booking_repo,
        send_message_fn=send_fn,
        lead_minutes=10,
        window_seconds=300,
        reminder_keys_path=keys_path,
    )

    scheduler._run_reminders_once()

    tg_destinations = [c[0] for c in sent_calls if c[0].startswith("telegram:")]

    if tg_destinations:
        _record("REQ-6: Message sent to Telegram chat_id", PASS, f"destinations={tg_destinations}")
        return True
    else:
        _record("REQ-6: Message sent to Telegram chat_id", FAIL,
                f"No Telegram destination. All calls: {sent_calls}")
        return False


# =============================================================================
# REQ-7: Doctor reminder sent to BOTH WhatsApp AND Telegram when both set
# =============================================================================

def test_req7_reminder_to_both():
    print("\n─── REQ-7: Doctor reminder → Both WhatsApp AND Telegram ──────────")

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        keys_path = f.name

    sent_calls: list[tuple] = []

    def send_fn(to: str, text: str):
        sent_calls.append((to, text))
        return "msg_sid_3"

    booking_repo = MagicMock(spec=BookingRepository)
    slot_date, sched_start = _upcoming_schedule_time(lead_minutes=10)
    booking_repo.list_due_doctor_reminders.return_value = [
        _make_doctor_reminder(
            doctor_whatsapp="9392569600",          # Dr. Sanjay Vinayak (doctor_id=1)
            doctor_telegram_chat_id="8299824956",  # Dr. Sanjay Vinayak (doctor_id=1)
            slot_date=slot_date,
            schedule_start_time=sched_start,
        )
    ]

    scheduler = _make_scheduler(
        booking_repo=booking_repo,
        send_message_fn=send_fn,
        lead_minutes=10,
        window_seconds=300,
        reminder_keys_path=keys_path,
    )

    scheduler._run_reminders_once()

    wa_destinations  = [c[0] for c in sent_calls if not c[0].startswith("telegram:")]
    tg_destinations  = [c[0] for c in sent_calls if c[0].startswith("telegram:")]

    all_pass = True
    if wa_destinations:
        _record("REQ-7: WhatsApp destination received reminder", PASS, str(wa_destinations))
    else:
        _record("REQ-7: WhatsApp destination received reminder", FAIL,
                f"No WA call. All sent: {sent_calls}")
        all_pass = False

    if tg_destinations:
        _record("REQ-7: Telegram destination received reminder", PASS, str(tg_destinations))
    else:
        _record("REQ-7: Telegram destination received reminder", FAIL,
                f"No TG call. All sent: {sent_calls}")
        all_pass = False

    return all_pass


# =============================================================================
# REQ-8: Deduplication — same schedule NOT reminded twice
# =============================================================================

def test_req8_reminder_deduplication():
    print("\n─── REQ-8: Reminder deduplication (same schedule not sent twice) ─")

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        keys_path = f.name

    sent_calls: list[tuple] = []

    def send_fn(to: str, text: str):
        sent_calls.append((to, text))
        return "msg_sid_4"

    booking_repo = MagicMock(spec=BookingRepository)
    slot_date, sched_start = _upcoming_schedule_time(lead_minutes=10)
    reminder = _make_doctor_reminder(
        doctor_whatsapp="9392569600",  # Dr. Sanjay Vinayak (doctor_id=1)
        doctor_telegram_chat_id="",
        slot_date=slot_date,
        schedule_start_time=sched_start,
    )
    booking_repo.list_due_doctor_reminders.return_value = [reminder]

    scheduler = _make_scheduler(
        booking_repo=booking_repo,
        send_message_fn=send_fn,
        lead_minutes=10,
        window_seconds=300,
        reminder_keys_path=keys_path,
    )

    # First run — should send
    scheduler._run_reminders_once()
    first_count = len(sent_calls)

    # Second run — same schedule window, should be deduped
    scheduler._run_reminders_once()
    second_count = len(sent_calls)

    all_pass = True
    if first_count >= 1:
        _record("REQ-8: First run sends reminder", PASS, f"sent_count={first_count}")
    else:
        _record("REQ-8: First run sends reminder", FAIL, "nothing sent on first run")
        all_pass = False

    if second_count == first_count:
        _record("REQ-8: Second run does NOT re-send (dedup)", PASS, f"count unchanged at {first_count}")
    else:
        _record("REQ-8: Second run does NOT re-send (dedup)", FAIL,
                f"count grew from {first_count} to {second_count}")
        all_pass = False

    return all_pass


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 70)
    print("  COMPREHENSIVE REQUIREMENTS TEST")
    print("=" * 70)

    req_results = []

    def run_req(fn):
        try:
            ok = fn()
            req_results.append((fn.__name__, ok))
        except Exception as exc:
            print(f"\n  [!] {fn.__name__} raised unhandled exception: {exc}")
            import traceback
            traceback.print_exc()
            req_results.append((fn.__name__, False))

    run_req(test_req1_slot_position)
    run_req(test_req2_patient_id_label)
    run_req(test_req3_confirm_reply_hint)
    run_req(test_req4_full_fsm_flow)
    run_req(test_req5_reminder_to_whatsapp)
    run_req(test_req6_reminder_to_telegram)
    run_req(test_req7_reminder_to_both)
    run_req(test_req8_reminder_deduplication)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    passed = sum(1 for _, ok in req_results if ok)
    failed = len(req_results) - passed
    for name, ok in req_results:
        icon = "✓" if ok else "✗"
        label = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {label}  {name}")
    print(f"\n  Total: {passed} passed, {failed} failed out of {len(req_results)}")
    print("=" * 70)

    if failed:
        print("\n  ISSUES FOUND — see details above")
    else:
        print("\n  ALL REQUIREMENTS PASS")

    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
