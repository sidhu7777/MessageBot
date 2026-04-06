import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import MySQLConfig
from src.repositories.conversation_repository import ConversationRepository


class _FakeCursor:
    def __init__(self, columns=None):
        self.executed = []
        self._columns = columns or []

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchall(self):
        return self._columns

    def fetchone(self):
        return None

    def close(self):
        return None


class _FakeConn:
    def __init__(self, columns=None):
        self.columns = columns or []
        self.cursors = []
        self.commits = 0

    def cursor(self, dictionary=False):
        cur = _FakeCursor(columns=self.columns)
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


def test_overflow_queue_schema_and_enqueue_sql_present() -> None:
    repo = ConversationRepository(
        MySQLConfig(user="u", password="p", host="h", port=3306, database="d")
    )
    fake_conn = _FakeConn()
    repo._connect = lambda: fake_conn  # type: ignore[method-assign]

    repo.ensure_schema()
    repo.enqueue_overflow_turn(
        inbound_sid="SID-1",
        from_number="whatsapp:+911111111111",
        body="hello",
        pre_state="INIT",
    )

    sql_text = "\n".join(q for c in fake_conn.cursors for (q, _) in c.executed).lower()
    assert "create table if not exists inbound_turn_queue" in sql_text
    assert "insert into inbound_turn_queue" in sql_text
    assert fake_conn.commits >= 2

