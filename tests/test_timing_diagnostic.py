"""
=============================================================================
  TIMING DIAGNOSTIC TEST  –  Where is the bot spending time?
=============================================================================
Mimics a REAL Telegram user booking flow end-to-end.
Measures wall-clock time for:
  • Each individual FSM step  (fsm.handle)
  • Every DB call              (BookingRepo + SchedulingRepo methods)
  • Every LLM call             (LLMClient.generate)

Run:
    $env:PYTHONUTF8=1; .\\venv\\Scripts\\python.exe tests\\test_timing_diagnostic.py
=============================================================================
"""

import functools
import logging
import os
import sys
import time
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.db.connection import parse_mysql_url
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository

logging.basicConfig(level=logging.WARNING)

# ─── Timing store ─────────────────────────────────────────────────────────────

class TimingStore:
    def __init__(self):
        self.records: List[dict] = []

    def add(self, category: str, name: str, duration_ms: float, detail: str = ""):
        self.records.append({
            "category": category,
            "name": name,
            "ms": duration_ms,
            "detail": detail,
        })

    def print_report(self):
        print("\n" + "═" * 78)
        print("  TIMING BREAKDOWN REPORT")
        print("═" * 78)

        # Group by category
        categories = {}
        for r in self.records:
            categories.setdefault(r["category"], []).append(r)

        total_all = sum(r["ms"] for r in self.records)

        for cat in ["FSM_STEP", "LLM", "DB"]:
            if cat not in categories:
                continue
            rows = categories[cat]
            total_cat = sum(r["ms"] for r in rows)
            pct = (total_cat / total_all * 100) if total_all else 0
            print(f"\n  [{cat}]  total={total_cat:.0f}ms  ({pct:.1f}% of all time)")
            print(f"  {'Name':<40} {'ms':>8}  {'Detail'}")
            print(f"  {'-'*40} {'-'*8}  {'-'*20}")
            for r in rows:
                flag = "  ⚠ SLOW" if r["ms"] > 2000 else ""
                print(f"  {r['name']:<40} {r['ms']:>8.0f}  {r['detail']}{flag}")

        print(f"\n  {'TOTAL WALL TIME':<40} {total_all:>8.0f}ms")
        print("═" * 78)

        # Summary verdict
        llm_total   = sum(r["ms"] for r in categories.get("LLM", []))
        db_total    = sum(r["ms"] for r in categories.get("DB",  []))
        fsm_total   = sum(r["ms"] for r in categories.get("FSM_STEP", []))
        overhead    = fsm_total - llm_total - db_total
        overhead    = max(0, overhead)

        print("\n  VERDICT:")
        print(f"    LLM calls     : {llm_total:>8.0f} ms  ({llm_total/total_all*100:.1f}%)")
        print(f"    DB calls      : {db_total:>8.0f} ms  ({db_total/total_all*100:.1f}%)")
        print(f"    FSM logic/net : {overhead:>8.0f} ms  ({overhead/total_all*100:.1f}% - pure Python)")
        print(f"    TOTAL         : {total_all:>8.0f} ms")

        bottleneck = max(
            [("LLM", llm_total), ("DB", db_total), ("FSM logic", overhead)],
            key=lambda x: x[1]
        )
        print(f"\n  ➜  BIGGEST BOTTLENECK: {bottleneck[0]} ({bottleneck[1]:.0f}ms)")
        print("═" * 78 + "\n")


TIMING = TimingStore()


# ─── Instrumentation wrappers ─────────────────────────────────────────────────

def _timed(category: str, base_name: str, fn, *args, detail_fn=None, **kwargs):
    """Call fn(*args, **kwargs), record timing, return result."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    detail = detail_fn(*args, **kwargs) if detail_fn else ""
    TIMING.add(category, base_name, elapsed_ms, detail)
    return result


def instrument_llm(llm_client: LLMClient):
    """Wrap LLMClient.generate to record every call."""
    original = llm_client.generate

    @functools.wraps(original)
    def wrapper(system_prompt: str, user_prompt: str):
        t0 = time.perf_counter()
        result = original(system_prompt, user_prompt)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        snippet = user_prompt[:60].replace("\n", " ")
        TIMING.add("LLM", "llm_client.generate", elapsed_ms, f"user={snippet!r}")
        return result

    llm_client.generate = wrapper


def instrument_repo_method(repo, method_name: str, category: str = "DB"):
    """Wrap a repo method to record timing."""
    original = getattr(repo, method_name)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = original(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        TIMING.add(category, f"{type(repo).__name__}.{method_name}", elapsed_ms)
        return result

    setattr(repo, method_name, wrapper)


def instrument_repos(booking_repo: BookingRepository, scheduling_repo: SchedulingRepository):
    """Instrument all key repository methods."""
    booking_methods = [
        "find_patient_name_by_phone_number",
        "find_patient_name_by_chat_user_id",
        "find_active_appointment_by_phone_number",
        "find_active_appointment_by_chat_user_id",
        "list_active_appointments_by_phone_number",
        "list_active_appointments_by_chat_user_id",
        "save_confirmed_appointment",
        "cancel_appointment",
    ]
    for m in booking_methods:
        if hasattr(booking_repo, m):
            instrument_repo_method(booking_repo, m)

    scheduling_methods = [
        "list_clinics_for_doctor",
        "list_available_dates",
        "list_available_times",
        "default_doctor_id",
    ]
    for m in scheduling_methods:
        if hasattr(scheduling_repo, m):
            instrument_repo_method(scheduling_repo, m, category="DB")


# ─── FSM factory ──────────────────────────────────────────────────────────────

def make_instrumented_fsm(chat_id: str, enable_llm: bool = True) -> Tuple[AppointmentFSM, LLMClient]:
    db_url = os.getenv("DATABASE_URL", "")
    assert db_url, "DATABASE_URL not set"
    config = parse_mysql_url(db_url)

    booking_repo = BookingRepository(config)
    scheduling_repo = SchedulingRepository(config)

    llm = LLMClient(model=os.getenv("LLM_MODEL", "qwen3:0.6b"), timeout_seconds=25.0)

    # Instrument before FSM is created
    instrument_llm(llm)
    instrument_repos(booking_repo, scheduling_repo)

    fsm = AppointmentFSM(
        llm_client=llm,
        enable_llm_polish=enable_llm,
        booking_repository=booking_repo,
        scheduling_repository=scheduling_repo,
        doctor_id=1,
        admin_id=1,
        chat_phone_number=chat_id,
    )
    return fsm, llm


# ─── Step helper with timing ──────────────────────────────────────────────────

def step(fsm: AppointmentFSM, user_input: str, label: str = "") -> str:
    before = fsm.state
    t0 = time.perf_counter()
    response = fsm.handle(user_input)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    TIMING.add(
        "FSM_STEP",
        f"handle({before}→{fsm.state})",
        elapsed_ms,
        f"input={user_input!r}"
    )

    flag = "  ⚠ SLOW" if elapsed_ms > 2000 else ""
    snippet = response.replace("\n", " │ ")[:80]
    print(f"  [{elapsed_ms:>6.0f}ms] {label or user_input!r:<22} {before!r:22} → {fsm.state!r:22} | {snippet!r}{flag}")
    return response


# ─── SCENARIO 1: New Telegram user, pure rule-based path (no LLM expected) ────

def run_scenario_new_patient_rule_based():
    print("\n" + "═" * 78)
    print("  SCENARIO 1: New Telegram patient — rule-based path (LLM disabled)")
    print("  Expectation: every step < 500ms (only DB + FSM logic)")
    print("═" * 78)

    TIMING.records.clear()
    fsm, _ = make_instrumented_fsm("telegram:TEST_NEW_DIAG_001", enable_llm=False)

    # Fetch real clinic/date/time from DB for dynamic inputs
    from src.repositories.scheduling_repository import SchedulingRepository
    from src.db.connection import parse_mysql_url
    config = parse_mysql_url(os.getenv("DATABASE_URL", ""))
    sr = SchedulingRepository(config)

    clinics = sr.list_clinics_for_doctor(doctor_id=1, admin_id=1)
    assert clinics, "No clinics in DB for doctor_id=1"
    clinic = clinics[0]
    print(f"  Using clinic: {clinic.clinic_name} (id={clinic.clinic_id})")

    step(fsm, "/start",        "/start")
    step(fsm, "book",          "book")
    step(fsm, "1",             "1=self")
    step(fsm, "Test Diagnose", "name")
    step(fsm, "9876543210",    "phone")
    step(fsm, "1",             "1=clinic")

    # Get real dates
    date_options = fsm._date_options() if hasattr(fsm, "_date_options") else []
    if date_options:
        step(fsm, "1",  "1=date")
    else:
        print("  [SKIP] No date options available in DB")

    # Get real times
    if fsm.state == "ASK_TIME":
        step(fsm, "1",  "1=time")

    if fsm.state == "CONFIRM":
        step(fsm, "2",  "2=no(skip confirm)")

    TIMING.print_report()


# ─── SCENARIO 2: Same flow but LLM ENABLED — measures real LLM cost ───────────

def run_scenario_with_llm_enabled():
    print("\n" + "═" * 78)
    print("  SCENARIO 2: Ambiguous first message — LLM IS triggered")
    print("  Expectation: INIT step will be slow due to LLM call")
    print("═" * 78)

    TIMING.records.clear()
    fsm, llm = make_instrumented_fsm("telegram:TEST_NEW_DIAG_002", enable_llm=True)

    # Check if Ollama is even running
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:11434", timeout=2)
        ollama_running = True
    except Exception:
        ollama_running = False
        print("  [WARNING] Ollama is NOT running at localhost:11434 — LLM calls will fail/skip")

    # Ambiguous message that WILL fall through all rules → triggers LLM
    step(fsm, "/start",                   "/start")
    step(fsm, "I need some help please",  "ambiguous→LLM")  # no rule match → LLM

    TIMING.print_report()

    if not ollama_running:
        print("  NOTE: Start Ollama to see real LLM timing: ollama serve")


# ─── SCENARIO 3: Known patient (Telegram chat_id exists in DB) ────────────────

def run_scenario_known_patient():
    print("\n" + "═" * 78)
    print("  SCENARIO 3: Known Telegram patient (Anant, chat_id=6935976617)")
    print("  Expectation: DB hydration at INIT, name/phone skipped")
    print("═" * 78)

    TIMING.records.clear()
    # Anant has telegram_chat_id=6935976617 and 1 BOOKED appointment in DB
    fsm, _ = make_instrumented_fsm("telegram:6935976617", enable_llm=False)

    step(fsm, "/start",  "/start")

    if fsm.state == "ASK_EXISTING_BOOKING_ACTION":
        print(f"  [OK] Existing booking detected, state={fsm.state}")
        step(fsm, "1",   "1=keep")
    elif fsm.state == "INIT":
        step(fsm, "book", "book")
        if fsm.state == "ASK_BOOKING_FOR":
            step(fsm, "1",  "1=self")
            # Known patient → name and phone should be skipped
            print(f"  State after self: {fsm.state} | known_name={fsm.known_patient_name!r}")

    TIMING.print_report()


# ─── SCENARIO 4: Component-level micro-benchmark ─────────────────────────────

def run_micro_benchmarks():
    print("\n" + "═" * 78)
    print("  SCENARIO 4: Component micro-benchmarks (DB only, isolated)")
    print("═" * 78)

    TIMING.records.clear()
    db_url = os.getenv("DATABASE_URL", "")
    config = parse_mysql_url(db_url)

    booking_repo = BookingRepository(config)
    scheduling_repo = SchedulingRepository(config)

    results = {}

    def bench(name, fn, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            result = f"ERROR: {e}"
        elapsed_ms = (time.perf_counter() - t0) * 1000
        results[name] = elapsed_ms
        flag = "  ⚠ SLOW" if elapsed_ms > 500 else ""
        print(f"  {name:<55} {elapsed_ms:>8.0f} ms{flag}")
        return result

    print(f"\n  {'Operation':<55} {'ms':>8}")
    print(f"  {'-'*55} {'-'*8}")

    bench("SchedulingRepo.list_clinics_for_doctor(doctor_id=1)",
          scheduling_repo.list_clinics_for_doctor, doctor_id=1, admin_id=1)

    clinics = scheduling_repo.list_clinics_for_doctor(doctor_id=1, admin_id=1)
    if clinics:
        clinic_id = clinics[0].clinic_id
        bench(f"SchedulingRepo.list_available_dates(clinic_id={clinic_id})",
              scheduling_repo.list_available_dates,
              doctor_id=1, clinic_id=clinic_id, admin_id=1)

        dates = scheduling_repo.list_available_dates(doctor_id=1, clinic_id=clinic_id, admin_id=1)
        if dates:
            bench(f"SchedulingRepo.list_available_times(date={dates[0]})",
                  scheduling_repo.list_available_times,
                  doctor_id=1, clinic_id=clinic_id, slot_date=dates[0], admin_id=1)

    bench("BookingRepo.find_patient_name_by_chat_user_id(6935976617)",
          booking_repo.find_patient_name_by_chat_user_id,
          chat_user_id="6935976617", admin_id=1, doctor_id=1)

    bench("BookingRepo.list_active_appointments_by_chat_user_id(6935976617)",
          booking_repo.list_active_appointments_by_chat_user_id,
          chat_user_id="6935976617", admin_id=1, doctor_id=1)

    # LLM micro-benchmark (only if Ollama is running)
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:11434", timeout=2)
        llm = LLMClient(model=os.getenv("LLM_MODEL", "qwen3:0.6b"), timeout_seconds=25.0)
        bench("LLMClient.generate (single call, simple prompt)",
              llm.generate,
              "Return YES or NO only.",
              "Is 'book appointment' a booking intent? User: book appointment")
    except Exception:
        print(f"  {'LLM (Ollama not running — skipped)':<55} {'N/A':>8}")

    print(f"\n  SUMMARY:")
    slowest = sorted(results.items(), key=lambda x: x[1], reverse=True)
    for name, ms in slowest:
        bar = "█" * min(40, int(ms / 50))
        print(f"    {name[:50]:<50} {ms:>8.0f}ms  {bar}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "█" * 78)
    print("  BOT TIMING DIAGNOSTIC  –  Finding where time is spent")
    print("█" * 78)

    run_micro_benchmarks()
    run_scenario_new_patient_rule_based()
    run_scenario_known_patient()
    run_scenario_with_llm_enabled()

    print("\nDiagnostic complete.")
