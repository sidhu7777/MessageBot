"""
UX Changes Validation Test
Run: $env:PYTHONUTF8=1; .\venv\Scripts\python.exe tests\test_recent_ux_changes.py
"""
import sys, time
from pathlib import Path
from unittest.mock import MagicMock
from typing import Optional, List
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption
from src.messages.templates import get_message
PASS = 0; FAIL = 0; TIMINGS = []

def check(label, ok, detail=""):
    global PASS, FAIL
    if ok: PASS += 1; print(f"  [PASS] {label}")
    else: FAIL += 1; print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))

def step(fsm, msg, label):
    t0 = time.perf_counter()
    r = fsm.handle(msg)
    TIMINGS.append({"step": label, "ms": (time.perf_counter()-t0)*1000})
    return r

def make_fsm(phone="telegram:111", known_name="TestPatient", known_phone=None,
             has_dates=True, slots=None, save_ok=True, clinic="Health Plus Clinic"):
    llm = MagicMock(spec=LLMClient)
    llm.generate.return_value = "BOOK_APPOINTMENT"
    repo = MagicMock()
    repo.list_active_appointments_by_chat_user_id.return_value = []
    repo.list_active_appointments_by_phone_number.return_value = []
    repo.find_patient_name_by_chat_user_id.return_value = known_name
    repo.find_patient_name_by_phone_number.return_value = known_name
    repo.find_patient_phone_by_chat_user_id.return_value = known_phone
    repo.get_doctor_display_name.return_value = "Sanjay"
    repo.default_admin_id.return_value = 1
    sv = MagicMock(); sv.ok = save_ok; sv.appointment_id = 42; sv.queue_number = None
    repo.save_confirmed_appointment.return_value = sv
    sch = MagicMock()
    sch.default_doctor_id.return_value = 1
    sch.default_doctor_id_by_username.return_value = 1
    sch.doctor_accept_days.return_value = 2
    sch.list_available_dates.return_value = ["2026-03-10","2026-03-11"] if has_dates else []
    sch.list_available_times.return_value = slots or ["10:00","10:20","10:40"]
    sch.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name=clinic, location="Noida", today_slots=5)]
    return AppointmentFSM(llm_client=llm, chat_phone_number=phone,
        booking_repository=repo, scheduling_repository=sch,
        doctor_id=1, admin_id=1, enable_llm_polish=False)

def drive_to_slot_prompt(fsm):
    """Drive FSM from fresh to the slot chooser prompt (returns prompt reply)."""
    step(fsm, "/start", "start")     # INIT: _welcome_greeting sets known_patient_name, state=INIT
    step(fsm, "1", "init_book")      # INIT "1" -> ASK_BOOKING_FOR
    step(fsm, "1", "self")           # ASK_BOOKING_FOR "1" known -> ASK_CLINIC
    step(fsm, "1", "clinic")         # ASK_CLINIC "1" -> ASK_DATE
    step(fsm, "1", "date")           # ASK_DATE "1" -> ASK_TIME (shows initial prompt)
    return step(fsm, "what slots", "time_prompt")  # -> builds slot cache, returns prompt

# ------- TEST 1 -------
def test_choose_slot_header():
    print("\n[TEST 1] choose_slot_header - all 3 languages + FSM")
    expected = {
        "en":"Please choose a slot:",
        "hi":"\u0915\u0943\u092a\u092f\u093e \u090f\u0915 \u0938\u094d\u0932\u0949\u091f \u091a\u0941\u0928\u0947\u0902:",
        "hinglish":"Please ek slot choose kariye:"}
    for lang, exp in expected.items():
        val = get_message(lang, "choose_slot_header")
        check(f"  {lang} header", val == exp, f"got:{val!r}")
    fsm = make_fsm()
    reply = drive_to_slot_prompt(fsm)
    check("  FSM says Please choose a slot:", "Please choose a slot:" in reply, f"reply:{reply[:140]!r}")
    check("  FSM NOT one-hour slot", "one-hour slot" not in reply, f"reply:{reply[:140]!r}")
    check("  Numbered options present", "1." in reply, f"reply:{reply[:140]!r}")

# ------- TEST 2 -------
def test_no_date_available():
    print("\n[TEST 2] no_date_available - dynamic clinic name")
    fsm = make_fsm(has_dates=False, clinic="Health Plus Clinic")
    step(fsm, "/start", "s2_start")
    step(fsm, "1", "s2_book")
    step(fsm, "1", "s2_self")
    reply = step(fsm, "1", "s2_clinic")  # select clinic -> no dates -> fires message
    check("  clinic name in msg", "Health Plus Clinic" in reply, f"reply:{reply[:250]!r}")
    check("  no this clinic fallback", "this clinic" not in reply, f"reply:{reply[:250]!r}")
    check("  says slot", "slot" in reply.lower(), f"reply:{reply[:250]!r}")
    check("  asks another clinic", "another clinic" in reply.lower() or "doosra" in reply.lower(), f"reply:{reply[:250]!r}")

# ------- TEST 3 -------
def test_confirmation_format():
    print("\n[TEST 3] Confirmation - success first, no old header")
    fsm = make_fsm()
    drive_to_slot_prompt(fsm)         # slot prompt shown
    step(fsm, "1", "t3_slot")         # pick slot -> CONFIRM shows summary
    reply = step(fsm, "1", "t3_confirm")  # confirm -> booked
    check("  successfully present", "successfully" in reply.lower(), f"reply:{reply[:350]!r}")
    check("  no Appointment request confirmed", "Appointment request confirmed" not in reply, f"reply:{reply[:350]!r}")
    check("  no confirm ho gaya", "confirm ho gaya" not in reply, f"reply:{reply[:350]!r}")
    check("  Name: present", "Name:" in reply, f"reply:{reply[:350]!r}")
    if "successfully" in reply.lower() and "Name:" in reply:
        check("  success before Name:", reply.lower().index("successfully") < reply.index("Name:"), f"reply:{reply[:350]!r}")
    check("  clinic in reply", "Health Plus Clinic" in reply, f"reply:{reply[:350]!r}")
    check("  ID 42 shown", "42" in reply, f"reply:{reply[:350]!r}")

# ------- TEST 4 -------
def test_telegram_phone():
    print("\n[TEST 4] Telegram known patient - phone from DB")
    fsm = make_fsm(phone="telegram:999", known_name="Ghrdftufg", known_phone="9876543210")
    step(fsm, "/start", "t4_start")
    step(fsm, "1", "t4_book")
    step(fsm, "1", "t4_self")
    check("  phone set in context", fsm.context.phone_number == "9876543210", f"got:{fsm.context.phone_number!r}")
    step(fsm, "1", "t4_clinic"); step(fsm, "1", "t4_date")
    step(fsm, "slots", "t4_time")
    summary = step(fsm, "1", "t4_summary")
    check("  Contact:None not in summary", "Contact: None" not in summary, f"summary:{summary[:300]!r}")
    check("  9876543210 in summary", "9876543210" in summary, f"summary:{summary[:300]!r}")

# ------- TEST 5 -------
def test_template_keys():
    print("\n[TEST 5] Template key integrity - all 3 languages")
    kw = dict(clinic_name="C",patient_name="A",phone_number="B",clinic_address="D",
              appointment_date="E",appointment_time="F",appointment_id="99",
              patient_type="New",age="30",gender="M",reason="Checkup")
    for lang in ["en","hi","hinglish"]:
        for key in ["choose_slot_header","no_date_available","confirmed","db_save_ok","clarify_intent"]:
            try:
                val = get_message(lang, key, **kw)
                check(f"  {lang}.{key}", bool(val))
            except Exception as e:
                check(f"  {lang}.{key}", False, str(e))

def print_latency():
    print("\n" + "="*65)
    print("  LATENCY (mocked DB+LLM, no Redis, pure FSM time)")
    print("="*65)
    total = sum(t["ms"] for t in TIMINGS)
    for t in TIMINGS:
        flag = " SLOW" if t["ms"] > 100 else ""
        name, ms = t["step"], t["ms"]
        print(f"  {name:<38} {ms:>8.2f}ms{flag}")
    print(f"  {'TOTAL':<38} {total:>8.2f}ms")
    print("="*65)
    print("  All fast (no Redis overhead)" if total < 1000 else "  Check slow steps")

if __name__ == "__main__":
    print("="*65); print("  UX CHANGES VALIDATION TEST"); print("="*65)
    test_choose_slot_header()
    test_no_date_available()
    test_confirmation_format()
    test_telegram_phone()
    test_template_keys()
    print_latency()
    print(f"\n  RESULT: {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL==0 else 1)
