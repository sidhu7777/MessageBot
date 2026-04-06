import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import MySQLConfig
from src.repositories.booking_repository import BookingRepository


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        return None


class _FakeConn:
    def __init__(self):
        self.cursors = []
        self.commits = 0

    def cursor(self, dictionary=False):
        cur = _FakeCursor()
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


def test_upsert_delivery_status_writes_both_tables() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    fake_conn = _FakeConn()
    repo._connect = lambda: fake_conn  # type: ignore[method-assign]

    repo.upsert_delivery_status(
        provider="twilio",
        provider_message_sid="SM123",
        channel="whatsapp",
        message_status="DELIVERED",
        to_number="whatsapp:+911234567890",
        from_number="whatsapp:+14155238886",
        error_code="",
        error_message="",
        payload_json='{"k":"v"}',
    )

    sql_text = "\n".join(q for c in fake_conn.cursors for (q, _) in c.executed).lower()
    assert "insert into message_delivery_status" in sql_text
    assert "update appointment_notification_log" in sql_text
    assert fake_conn.commits >= 1

