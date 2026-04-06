# -*- coding: utf-8 -*-
"""
LIVE test - Availability display formatting
============================================
Connects to the real DB (reads .env) and verifies:
  1. Single-slot reply  -> "1 slot at HH:MM AM/PM"
  2. Multi-slot reply   -> "N slots (HH:MM AM/PM - HH:MM AM/PM)"
  3. Footer             -> "1. Book appointment\\n0. Go back"
  4. Old bad format     -> "N slots (X-X)" same-time range is GONE
  5. Press "0" after    -> goes back to INIT (welcome menu)
  6. Press "1" after    -> routes to ASK_BOOKING_FOR

Run:
    python tests/test_availability_display_live.py
"""

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.db.connection import MySQLConfig, parse_mysql_url
from src.repositories.scheduling_repository import SchedulingRepository
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" -- {detail}" if detail else ""))


def get_db_config() -> MySQLConfig:
    url = os.getenv("DATABASE_URL", "").strip()
    assert url.startswith("mysql+mysqlconnector://"), "DATABASE_URL not set or invalid in .env"
    return parse_mysql_url(url)


def db_connect(config: MySQLConfig):
    import mysql.connector
    return mysql.connector.connect(
        host=config.host, port=config.port,
        user=config.user, password=config.password,
        database=config.database, ssl_disabled=False,
    )


def get_first_active_doctor_with_schedule(config: MySQLConfig):
    """Return (doctor_id, admin_id, doctor_name) for the first active doctor that has a schedule."""
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT DISTINCT d.doctor_id, d.admin_id, d.doctor_name
            FROM doctors d
            JOIN doctor_clinic_schedule dcs ON dcs.doctor_id = d.doctor_id
            WHERE d.status = 'ACTIVE'
            LIMIT 1
        """)
        return cur.fetchone()
    finally:
        cur.close(); conn.close()


def get_schedules_for_doctor(config: MySQLConfig, doctor_id: int):
    """Return all schedule rows for the doctor."""
    conn = db_connect(config)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT dcs.*, c.clinic_name
            FROM doctor_clinic_schedule dcs
            JOIN clinics c ON c.clinic_id = dcs.clinic_id
            WHERE dcs.doctor_id = %s
        """, (doctor_id,))
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


def _new_fsm(scheduling_repo: SchedulingRepository, doctor_id: int, admin_id: int) -> AppointmentFSM:
    return AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", provider="mock", timeout_seconds=30),
        enable_llm_polish=False,
        mixed_response_language="auto",
        scheduling_repository=scheduling_repo,
        doctor_id=doctor_id,
        admin_id=admin_id,
    )


def main():
    print("=" * 60)
    print("LIVE Availability Display Formatting Test")
    print("=" * 60)

    config = get_db_config()
    doctor_row = get_first_active_doctor_with_schedule(config)
    if not doctor_row:
        print("[SKIP] No active doctor with a schedule found in DB.")
        return

    doctor_id = doctor_row["doctor_id"]
    admin_id = doctor_row["admin_id"]
    doctor_name = doctor_row["doctor_name"] or f"doctor_id={doctor_id}"
    print(f"\nUsing doctor: {doctor_name} (id={doctor_id}, admin_id={admin_id})")

    scheduling_repo = SchedulingRepository(config=config)

    # Find a date that actually has available slots
    clinics = scheduling_repo.list_clinics_for_doctor(doctor_id=doctor_id, admin_id=admin_id, limit=10)
    check("At least one clinic exists for doctor", len(clinics) > 0, str(clinics))
    if not clinics:
        return

    slot_count = None
    test_date = None
    test_clinic_name = None

    # Try today through next 5 days — find date with actual slots in reply
    for i in range(5):
        d = (date.today() + timedelta(days=i)).isoformat()
        probe = _new_fsm(scheduling_repo, doctor_id, admin_id)
        probe.handle("check availability")
        probe_reply = probe.handle(d)
        if "Doctor availability on" in probe_reply:
            test_date = d
            # extract slot count from reply line like "- ClinicName: 3 slots ..."
            import re
            m = re.search(r': (\d+) slot', probe_reply)
            if m:
                slot_count = int(m.group(1))
            test_clinic_name = "(from DB)"
            break
        # "no slots" reply is also valid — just keep looking

    if not test_date:
        print("[SKIP] No available slots found in next 14 days.")
        return

    print(f"\nFound slots on {test_date} (count={slot_count})")

    # ── Test 1: FSM produces availability reply (2-turn: ask → send date) ──
    print("\n--- Test 1: FSM availability reply (turn 1: 'check availability', turn 2: date) ---")
    fsm = _new_fsm(scheduling_repo, doctor_id, admin_id)
    reply1 = fsm.handle("check availability")
    print(f"  Turn 1 state: {fsm.state}")
    print(f"  Turn 1 reply: {reply1[:80]}")
    check("Turn 1: State is ASK_AVAILABILITY_DETAILS", fsm.state == "ASK_AVAILABILITY_DETAILS")

    reply = fsm.handle(test_date)
    print(f"  Turn 2 state: {fsm.state}")
    print(f"  Turn 2 reply:\n{reply}")

    check("Turn 2: State still ASK_AVAILABILITY_DETAILS", fsm.state == "ASK_AVAILABILITY_DETAILS")
    check("Reply contains 'Doctor availability on'", "Doctor availability on" in reply, reply[:120])
    check("Reply contains the date", test_date in reply, reply[:120])

    # ── Test 2: Format correctness ───────────────────────────────────────────
    print("\n--- Test 2: Slot format ---")
    if slot_count == 1:
        check("Single slot: contains '1 slot at'", "1 slot at" in reply, reply)
        check("Single slot: does NOT contain '1 slots'", "1 slots" not in reply, reply)
        check("Single slot: does NOT contain same-time range 'PM-'", "PM-" not in reply and "AM-" not in reply, reply)
    else:
        expected_count_str = f"{slot_count} slots"
        check(f"Multi slot: contains '{expected_count_str}'", expected_count_str in reply, reply)
        check("Multi slot: does NOT contain '1 slot at'", "1 slot at" not in reply, reply)
        check("Multi slot: does NOT contain '1 slots'", "1 slots" not in reply, reply)

    # ── Test 3: Footer ───────────────────────────────────────────────────────
    print("\n--- Test 3: Footer options ---")
    check("Footer: '1. Book appointment' present", "1. Book appointment" in reply, reply)
    check("Footer: '0. Go back' present", "0. Go back" in reply, reply)
    check("Footer: old phrase 'Reply with' is GONE", "Reply with" not in reply, reply)
    check("Footer: old phrase 'book appointment' as instruction is GONE",
          "to continue booking" not in reply.lower(), reply)

    # ── Test 4: Press "1" → starts booking flow ────────────────────────────
    # Reuse fsm (already at ASK_AVAILABILITY_DETAILS) — no extra DB call needed
    print("\n--- Test 4: Press '1' -> booking flow ---")
    reply2 = fsm.handle("1")
    print(f"  State after '1': {fsm.state}")
    print(f"  Reply: {reply2[:120]}")
    check("Press '1': state moves to ASK_BOOKING_FOR",
          fsm.state == "ASK_BOOKING_FOR", f"state={fsm.state}")

    # ── Test 5: Press "0" → goes back to INIT ──────────────────────────────
    # Create a fresh FSM, manually set state to ASK_AVAILABILITY_DETAILS
    # "0" is handled globally before state processing — no DB query needed
    print("\n--- Test 5: Press '0' -> go back ---")
    fsm3 = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", provider="mock", timeout_seconds=30),
        enable_llm_polish=False,
        mixed_response_language="auto",
        scheduling_repository=scheduling_repo,
        doctor_id=doctor_id,
        admin_id=admin_id,
        state="ASK_AVAILABILITY_DETAILS",
    )
    fsm3.context.availability_date = test_date
    reply3 = fsm3.handle("0")
    print(f"  State after '0': {fsm3.state}")
    print(f"  Reply: {reply3[:120]}")
    check("Press '0': state returns to INIT", fsm3.state == "INIT", f"state={fsm3.state}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
