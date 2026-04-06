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
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        return None


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.cursors = []

    def cursor(self, dictionary=False):
        cur = _FakeCursor(self.row)
        self.cursors.append(cur)
        return cur

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def test_overflow_and_notification_stats_methods() -> None:
    conv = ConversationRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conv.ensure_schema = lambda: None  # type: ignore[method-assign]
    conv._connect = lambda: _FakeConn({"queued": 4, "processing": 1, "dead": 2})  # type: ignore[method-assign]
    conv_stats = conv.overflow_queue_stats()
    assert conv_stats == {"queued": 4, "processing": 1, "dead": 2}

    book = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    book._connect = lambda: _FakeConn({"queued": 8, "dead": 3})  # type: ignore[method-assign]
    notif_stats = book.notification_queue_stats()
    assert notif_stats == {"queued": 8, "dead": 3}

