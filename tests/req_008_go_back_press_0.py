"""
REQ-008: Press 0 / Go-Back Navigation — Known & Unknown Patient Flows
Verifies that pressing "0" at each FSM state correctly navigates to the
previous state for both unknown patients and known patients (self-booking).

Go-back map (from _handle_go_back):
  ASK_BOOKING_FOR          → INIT
  ASK_NAME                 → ASK_BOOKING_FOR
  ASK_PHONE                → ASK_NAME
  ASK_CLINIC  (unknown)    → ASK_PHONE
  ASK_CLINIC  (known self) → ASK_BOOKING_FOR  ← skips name+phone (auto-filled)
  ASK_DATE                 → ASK_CLINIC
  ASK_TIME                 → ASK_DATE
  CONFIRM                  → ASK_TIME
  ASK_CHANGE_FIELD         → CONFIRM
  INIT                     → (no go-back — stays INIT)

Run: python tests/req_008_go_back_press_0.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM, AppointmentContext
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        detail_str = f" -- {detail}" if detail else ""
        print(f"  [FAIL] {label}{detail_str}")


def make_fsm(patient_name: str | None = None, phone: str = "telegram:999000") -> AppointmentFSM:
    """Base FSM factory. Set fsm.state and extra attributes after calling."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_repo = MagicMock()
    mock_sched = MagicMock()

    mock_repo.list_active_appointments_by_chat_user_id.return_value = []
    mock_repo.list_active_appointments_by_phone_number.return_value = []
    mock_repo.find_patient_name_by_chat_user_id.return_value = patient_name
    mock_repo.find_patient_name_by_phone_number.return_value = patient_name
    mock_repo.get_doctor_display_name.return_value = "Dr. Sanjay"
    mock_repo.default_admin_id.return_value = 1

    mock_sched.default_doctor_id.return_value = 1
    mock_sched.default_doctor_id_by_username.return_value = 1
    mock_sched.doctor_accept_days.return_value = 2
    mock_sched.list_available_dates.return_value = ["2026-03-01", "2026-03-02"]
    mock_sched.list_available_times.return_value = ["10:00", "10:30", "11:00"]
    mock_sched.list_clinics_for_doctor.return_value = [
        ClinicOption(clinic_id=1, clinic_name="City Care Clinic", location="MG Road", today_slots=5),
        ClinicOption(clinic_id=2, clinic_name="Sunrise Health", location="KPHB", today_slots=3),
    ]

    fsm = AppointmentFSM(
        llm_client=mock_llm,
        mixed_response_language="en",
        enable_llm_polish=False,
        booking_repository=mock_repo,
        scheduling_repository=mock_sched,
    )
    fsm.state = "INIT"
    fsm.context = AppointmentContext()
    fsm.chat_phone_number = phone
    fsm.doctor_id = 1
    fsm.admin_id = 1
    return fsm


# ─── Test 1: Unknown patient — ASK_BOOKING_FOR → 0 → INIT ────────────────────

def test_ask_booking_for_back_to_init():
    print("\n[TEST] Unknown patient: ASK_BOOKING_FOR + '0' → INIT")
    fsm = make_fsm()
    fsm.state = "ASK_BOOKING_FOR"

    reply = fsm.handle("0")

    check("state is INIT after pressing 0", fsm.state == "INIT",
          f"actual={fsm.state}")
    check("reply is non-empty", bool(reply.strip()))


# ─── Test 2: Unknown patient — ASK_NAME → 0 → ASK_BOOKING_FOR ───────────────

def test_ask_name_back_to_ask_booking_for():
    print("\n[TEST] Unknown patient: ASK_NAME + '0' → ASK_BOOKING_FOR")
    fsm = make_fsm()
    fsm.state = "ASK_NAME"

    reply = fsm.handle("0")

    check("state is ASK_BOOKING_FOR after pressing 0", fsm.state == "ASK_BOOKING_FOR",
          f"actual={fsm.state}")
    check("reply contains booking-for options (1./2.)", "1." in reply and "2." in reply,
          f"reply={reply[:120]}")


# ─── Test 3: Unknown patient — ASK_PHONE → 0 → ASK_NAME ─────────────────────

def test_ask_phone_back_to_ask_name():
    print("\n[TEST] Unknown patient: ASK_PHONE + '0' → ASK_NAME")
    fsm = make_fsm()
    fsm.state = "ASK_PHONE"

    reply = fsm.handle("0")

    check("state is ASK_NAME after pressing 0", fsm.state == "ASK_NAME",
          f"actual={fsm.state}")
    check("reply mentions name", "name" in reply.lower(),
          f"reply={reply[:120]}")


# ─── Test 4: Unknown patient — ASK_CLINIC → 0 → ASK_PHONE ───────────────────

def test_unknown_ask_clinic_back_to_ask_phone():
    print("\n[TEST] Unknown patient: ASK_CLINIC + '0' → ASK_PHONE")
    fsm = make_fsm(patient_name=None)
    fsm.state = "ASK_CLINIC"
    # unknown patient — booking_for_self may be True/False, known_patient_name must be None
    fsm.known_patient_name = None

    reply = fsm.handle("0")

    check("state is ASK_PHONE after pressing 0", fsm.state == "ASK_PHONE",
          f"actual={fsm.state}")
    check("reply asks for phone / contact", "phone" in reply.lower() or "contact" in reply.lower() or "number" in reply.lower(),
          f"reply={reply[:120]}")


# ─── Test 5: Known patient self — ASK_CLINIC → 0 → ASK_BOOKING_FOR ──────────

def test_known_self_ask_clinic_back_to_ask_booking_for():
    print("\n[TEST] Known patient self-booking: ASK_CLINIC + '0' → ASK_BOOKING_FOR (skips phone)")
    fsm = make_fsm(patient_name="Vineeth Kumar")
    fsm.state = "ASK_CLINIC"
    fsm.known_patient_name = "Vineeth Kumar"
    fsm.booking_for_self = True

    reply = fsm.handle("0")

    check("state is ASK_BOOKING_FOR (not ASK_PHONE)", fsm.state == "ASK_BOOKING_FOR",
          f"actual={fsm.state}")
    check("reply contains booking-for options (1./2.)", "1." in reply and "2." in reply,
          f"reply={reply[:120]}")
    check("state is NOT ASK_PHONE (name+phone were auto-filled, skip them)", fsm.state != "ASK_PHONE")


# ─── Test 6: ASK_DATE → 0 → ASK_CLINIC ──────────────────────────────────────

def test_ask_date_back_to_ask_clinic():
    print("\n[TEST] ASK_DATE + '0' → ASK_CLINIC")
    fsm = make_fsm()
    fsm.state = "ASK_DATE"
    fsm.context.clinic_id = "1"

    reply = fsm.handle("0")

    check("state is ASK_CLINIC after pressing 0", fsm.state == "ASK_CLINIC",
          f"actual={fsm.state}")
    check("reply contains clinic options", "City Care" in reply or "Sunrise" in reply or "1." in reply,
          f"reply={reply[:200]}")


# ─── Test 7: ASK_TIME → 0 → ASK_DATE ────────────────────────────────────────

def test_ask_time_back_to_ask_date():
    print("\n[TEST] ASK_TIME + '0' → ASK_DATE")
    fsm = make_fsm()
    fsm.state = "ASK_TIME"
    fsm.context.clinic_id = "1"
    fsm.context.appointment_date = "2026-03-01"

    reply = fsm.handle("0")

    check("state is ASK_DATE after pressing 0", fsm.state == "ASK_DATE",
          f"actual={fsm.state}")
    check("reply mentions a date (Mar or 2026)", "2026" in reply or "Mar" in reply or "march" in reply.lower(),
          f"reply={reply[:200]}")


# ─── Test 8: CONFIRM → 0 → ASK_TIME ─────────────────────────────────────────

def test_confirm_back_to_ask_time():
    print("\n[TEST] CONFIRM + '0' → ASK_TIME")
    fsm = make_fsm()
    fsm.state = "CONFIRM"
    fsm.context.appointment_date = "2026-03-01"
    fsm.context.clinic_id = "1"
    # pre-populate time cache so _initial_time_prompt doesn't return no_time_available
    fsm.time_options_cache = ["10:00", "10:30", "11:00"]
    fsm.time_hour_options_cache = ["10", "11"]

    reply = fsm.handle("0")

    check("state is ASK_TIME after pressing 0", fsm.state == "ASK_TIME",
          f"actual={fsm.state}")
    check("reply contains time options", "10" in reply or "11" in reply or "time" in reply.lower(),
          f"reply={reply[:200]}")


# ─── Test 9: ASK_CHANGE_FIELD → 0 → CONFIRM ─────────────────────────────────

def test_ask_change_field_back_to_confirm():
    print("\n[TEST] ASK_CHANGE_FIELD + '0' → CONFIRM")
    fsm = make_fsm()
    fsm.state = "ASK_CHANGE_FIELD"
    fsm.context.patient_name = "Vineeth"
    fsm.context.phone_number = "9876543210"
    fsm.context.clinic_name = "City Care Clinic"
    fsm.context.clinic_address = "MG Road"
    fsm.context.appointment_date = "2026-03-01"
    fsm.context.appointment_time = "10:00"

    reply = fsm.handle("0")

    check("state is CONFIRM after pressing 0", fsm.state == "CONFIRM",
          f"actual={fsm.state}")
    check("reply contains booking summary (patient name)", "Vineeth" in reply,
          f"reply={reply[:300]}")
    check("reply contains appointment date", "2026-03-01" in reply or "Mar" in reply,
          f"reply={reply[:300]}")


# ─── Test 10: INIT → 0 → stays INIT (no go-back at start) ───────────────────

def test_init_press_0_stays_init():
    print("\n[TEST] INIT + '0' → still INIT (no previous state)")
    fsm = make_fsm()
    fsm.state = "INIT"

    reply = fsm.handle("0")

    check("state is still INIT (go-back has no effect at start)", fsm.state == "INIT",
          f"actual={fsm.state}")
    check("some reply returned", bool(reply.strip()))


# ─── Test 11: Full go-back chain — unknown patient walks back all the way ─────

def test_full_chain_unknown_patient():
    print("\n[TEST] Unknown patient: press 0 from CONFIRM all the way back to INIT")
    fsm = make_fsm(patient_name=None)

    # Set FSM to CONFIRM state with full context
    fsm.state = "CONFIRM"
    fsm.context.patient_name = "Ravi Kumar"
    fsm.context.phone_number = "9876543210"
    fsm.context.clinic_id = "1"
    fsm.context.clinic_name = "City Care Clinic"
    fsm.context.clinic_address = "MG Road"
    fsm.context.appointment_date = "2026-03-01"
    fsm.context.appointment_time = "10:00"
    fsm.time_options_cache = ["10:00", "10:30"]
    fsm.time_hour_options_cache = ["10"]
    fsm.booking_for_self = False
    fsm.known_patient_name = None

    # CONFIRM → 0 → ASK_TIME
    fsm.handle("0")
    check("CONFIRM → 0 → ASK_TIME", fsm.state == "ASK_TIME",
          f"step1 state={fsm.state}")

    # ASK_TIME → 0 → ASK_DATE
    fsm.handle("0")
    check("ASK_TIME → 0 → ASK_DATE", fsm.state == "ASK_DATE",
          f"step2 state={fsm.state}")

    # ASK_DATE → 0 → ASK_CLINIC
    fsm.handle("0")
    check("ASK_DATE → 0 → ASK_CLINIC", fsm.state == "ASK_CLINIC",
          f"step3 state={fsm.state}")

    # ASK_CLINIC → 0 → ASK_PHONE  (unknown patient)
    fsm.handle("0")
    check("ASK_CLINIC → 0 → ASK_PHONE (unknown)", fsm.state == "ASK_PHONE",
          f"step4 state={fsm.state}")

    # ASK_PHONE → 0 → ASK_NAME
    fsm.handle("0")
    check("ASK_PHONE → 0 → ASK_NAME", fsm.state == "ASK_NAME",
          f"step5 state={fsm.state}")

    # ASK_NAME → 0 → ASK_BOOKING_FOR
    fsm.handle("0")
    check("ASK_NAME → 0 → ASK_BOOKING_FOR", fsm.state == "ASK_BOOKING_FOR",
          f"step6 state={fsm.state}")

    # ASK_BOOKING_FOR → 0 → INIT
    fsm.handle("0")
    check("ASK_BOOKING_FOR → 0 → INIT", fsm.state == "INIT",
          f"step7 state={fsm.state}")


# ─── Test 12: Full go-back chain — known patient (shorter path) ───────────────

def test_full_chain_known_patient_self():
    print("\n[TEST] Known patient self-booking: press 0 from CONFIRM — skips phone/name")
    fsm = make_fsm(patient_name="Vineeth Kumar")

    # Set FSM to CONFIRM state as known patient self-booking
    fsm.state = "CONFIRM"
    fsm.context.patient_name = "Vineeth Kumar"
    fsm.context.phone_number = "9876543210"
    fsm.context.clinic_id = "1"
    fsm.context.clinic_name = "City Care Clinic"
    fsm.context.clinic_address = "MG Road"
    fsm.context.appointment_date = "2026-03-01"
    fsm.context.appointment_time = "10:00"
    fsm.time_options_cache = ["10:00", "10:30"]
    fsm.time_hour_options_cache = ["10"]
    fsm.booking_for_self = True
    fsm.known_patient_name = "Vineeth Kumar"

    # CONFIRM → 0 → ASK_TIME
    fsm.handle("0")
    check("CONFIRM → 0 → ASK_TIME", fsm.state == "ASK_TIME",
          f"step1 state={fsm.state}")

    # ASK_TIME → 0 → ASK_DATE
    fsm.handle("0")
    check("ASK_TIME → 0 → ASK_DATE", fsm.state == "ASK_DATE",
          f"step2 state={fsm.state}")

    # ASK_DATE → 0 → ASK_CLINIC
    fsm.handle("0")
    check("ASK_DATE → 0 → ASK_CLINIC", fsm.state == "ASK_CLINIC",
          f"step3 state={fsm.state}")

    # ASK_CLINIC → 0 → ASK_BOOKING_FOR  (known self: name+phone were auto-filled, skip them)
    fsm.handle("0")
    check("ASK_CLINIC → 0 → ASK_BOOKING_FOR (not ASK_PHONE)", fsm.state == "ASK_BOOKING_FOR",
          f"step4 state={fsm.state}")

    # ASK_BOOKING_FOR → 0 → INIT
    fsm.handle("0")
    check("ASK_BOOKING_FOR → 0 → INIT", fsm.state == "INIT",
          f"step5 state={fsm.state}")


# ─── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("REQ-008: Press 0 / Go-Back Navigation — Known & Unknown Patient")
    print("=" * 60)

    test_ask_booking_for_back_to_init()
    test_ask_name_back_to_ask_booking_for()
    test_ask_phone_back_to_ask_name()
    test_unknown_ask_clinic_back_to_ask_phone()
    test_known_self_ask_clinic_back_to_ask_booking_for()
    test_ask_date_back_to_ask_clinic()
    test_ask_time_back_to_ask_date()
    test_confirm_back_to_ask_time()
    test_ask_change_field_back_to_confirm()
    test_init_press_0_stays_init()
    test_full_chain_unknown_patient()
    test_full_chain_known_patient_self()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)

    sys.exit(0 if FAIL == 0 else 1)
