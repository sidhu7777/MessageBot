"""Check upcoming appointments and schedules in the DB."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.db.connection import parse_mysql_url
config = parse_mysql_url(os.environ["DATABASE_URL"])
from src.repositories.booking_repository import BookingRepository

repo = BookingRepository(config)
conn = repo._connect()
cur = conn.cursor(dictionary=True)

appt_table = repo._appointment_table()
print(f"Appointment table: {appt_table}")

# All upcoming appointments
cur.execute(f"""
    SELECT a.appointment_id, a.appointment_date, a.start_time, a.status,
           d.doctor_name, d.chat_id, d.whatsapp_number
    FROM {appt_table} a
    LEFT JOIN doctors d ON d.doctor_id = a.doctor_id
    WHERE a.appointment_date >= CURDATE()
    ORDER BY a.appointment_date, a.start_time
    LIMIT 10
""")
rows = cur.fetchall()
print(f"Upcoming appointments: {len(rows)}")
for r in rows:
    print(f"  {r}")

# doctor_clinic_schedule
cur.execute("SELECT * FROM doctor_clinic_schedule LIMIT 10")
rows2 = cur.fetchall()
print(f"\ndoctor_clinic_schedule rows: {len(rows2)}")
for r in rows2:
    print(f"  {r}")
