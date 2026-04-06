import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import MySQLConfig
from src.repositories.booking_repository import BookingRepository
from src.repositories.conversation_repository import ConversationRepository


class _FakeCursor:
    def __init__(self, rows=None):
        self.executed = []
        self._rows = rows or []
        self._idx = 0

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def close(self):
        return None


class _FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.cursors = []
        self.commits = 0

    def cursor(self, dictionary=False):
        cur = _FakeCursor(rows=self.rows)
        self.cursors.append(cur)
        return cur

    def start_transaction(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


def test_claim_queries_include_locking_clauses() -> None:
    # conversation repo claim SQL
    conv = ConversationRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conv_rows = [{"queue_id": 1, "inbound_sid": "S1", "from_number": "telegram:1", "body": "hi", "pre_state": "INIT", "attempt_count": 0}]
    conv_conn = _FakeConn(rows=conv_rows)
    conv._connect = lambda: conv_conn  # type: ignore[method-assign]
    conv.ensure_schema = lambda: None  # type: ignore[method-assign]
    conv.claim_overflow_turns(limit=1, worker_id="w1")

    conv_sql = "\n".join(q for c in conv_conn.cursors for (q, _) in c.executed).lower()
    assert "for update" in conv_sql

    # booking repo claim SQL
    book = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    book_conn_claim = _FakeConn(rows=[{"notification_id": 11}])
    book_conn_load = _FakeConn(rows=[])
    calls = {"n": 0}

    def _connect_seq():
        calls["n"] += 1
        return book_conn_claim if calls["n"] == 1 else book_conn_load

    book._connect = _connect_seq  # type: ignore[method-assign]
    book._appointment_table = lambda: "appointment"  # type: ignore[method-assign]
    book._use_appointment_mode = lambda: True  # type: ignore[method-assign]
    book.claim_pending_notification_events(limit=1, worker_id="w2")

    book_sql = "\n".join(q for c in book_conn_claim.cursors for (q, _) in c.executed).lower()
    assert "for update" in book_sql

