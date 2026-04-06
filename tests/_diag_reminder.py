"""Quick diagnostic: shows what list_due_doctor_reminders returns and window timing."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.db.connection import parse_mysql_url
from src.repositories.booking_repository import BookingRepository
from datetime import datetime

LEAD_MINUTES = int(os.getenv("DOCTOR_REMINDER_LEAD_MINUTES", "10"))
WINDOW_SECONDS = int(os.getenv("DOCTOR_REMINDER_WINDOW_SECONDS", "30"))

db_url = os.getenv("DATABASE_URL", "")
assert db_url, "DATABASE_URL not set"
config = parse_mysql_url(db_url)
repo = BookingRepository(config)
rows = repo.list_due_doctor_reminders(lookahead_minutes=max(120, LEAD_MINUTES + 120))
now = datetime.now()

print(f"Current time (local): {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"LEAD_MINUTES={LEAD_MINUTES}  WINDOW_SECONDS={WINDOW_SECONDS}")
print(f"Due reminder rows fetched from DB: {len(rows)}")
print()

# Check dedup keys file
import json
keys_path = os.path.join("data", "doctor_reminder_keys.jsonl")
seen_keys = set()
if os.path.exists(keys_path):
    with open(keys_path, encoding="utf-8") as f:
        for line in f:
            try:
                seen_keys.add(json.loads(line.strip())["key"])
            except Exception:
                pass
print(f"Dedup keys already stored: {len(seen_keys)}")
print()

for r in rows:
    print(f"  appt_id={r.appointment_id}  patient={r.patient_name}")
    print(f"    slot_date={r.slot_date}  slot_time={r.slot_time}")
    print(f"    sched_start={r.schedule_start_time}  sched_end={r.schedule_end_time}  sched_id={r.schedule_id}")
    print(f"    doctor_whatsapp={r.doctor_whatsapp!r}  doctor_telegram_chat_id={r.doctor_telegram_chat_id!r}")

    if r.schedule_start_time and r.slot_date:
        try:
            window_start = datetime.strptime(f"{r.slot_date} {r.schedule_start_time}", "%Y-%m-%d %H:%M")
            delta = int((window_start - now).total_seconds())
            center = LEAD_MINUTES * 60
            in_window = center - WINDOW_SECONDS <= delta <= center + WINDOW_SECONDS
            print(f"    delta_to_sched_start={delta}s  required={center-WINDOW_SECONDS}..{center+WINDOW_SECONDS}  IN_WINDOW={in_window}")
        except Exception as e:
            print(f"    timing parse error: {e}")

    dedup_key = f"doctor-schedule-reminder:{r.slot_date}:{r.schedule_id}:{r.schedule_start_time}:{r.schedule_end_time}"
    print(f"    dedup_key={dedup_key}")
    print(f"    already_sent={dedup_key in seen_keys}")
    print()

if not rows:
    print("  → No appointments found within the lookahead window.")
    print("     Either no BOOKED/CONFIRMED appointments in the next ~2 hours,")
    print("     or the schedule join returned no rows (check doctor_clinic_schedule table).")
