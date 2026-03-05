from dotenv import load_dotenv
load_dotenv()
from src.db_store import _config_from_env
from src.db.connection import connect_mysql

cfg = _config_from_env()
conn = connect_mysql(cfg)
cur = conn.cursor(dictionary=True)

print("=== doctor_whatsapp_numbers schema ===")
cur.execute("DESCRIBE doctor_whatsapp_numbers")
for r in cur.fetchall():
    print(r)

print("\n=== doctor_whatsapp_numbers data ===")
cur.execute("SELECT * FROM doctor_whatsapp_numbers LIMIT 10")
for r in cur.fetchall():
    print(r)

print("\n=== doctors columns ===")
cur.execute("SHOW COLUMNS FROM doctors")
for r in cur.fetchall():
    print(r)

cur.close()
conn.close()
