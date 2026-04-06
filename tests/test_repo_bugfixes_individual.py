import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import MySQLConfig
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository


class _FakeCursor:
    def __init__(self, rows=None):
        self.executed = []
        self._rows = rows or []
        self._fetchall_calls = 0
        self._fetchone_idx = 0

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchall(self):
        # Support either a static list or a list of lists as side effects.
        if self._rows and isinstance(self._rows[0], list):
            idx = min(self._fetchall_calls, len(self._rows) - 1)
            self._fetchall_calls += 1
            return self._rows[idx]
        return self._rows

    def fetchone(self):
        rows = self.fetchall()
        if self._fetchone_idx < len(rows):
            row = rows[self._fetchone_idx]
            self._fetchone_idx += 1
            return row
        return None

    def close(self):
        return None


class _FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, dictionary=False):
        cur = _FakeCursor(rows=self.rows)
        self.cursors.append(cur)
        return cur

    def start_transaction(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None


def _sql_text(conn: _FakeConn) -> str:
    return "\n".join(q for c in conn.cursors for (q, _) in c.executed).lower()


def test_log_notification_event_new_rows_have_null_next_retry_at() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conn = _FakeConn()
    repo._connect = lambda: conn  # type: ignore[method-assign]

    repo.log_notification_event(
        appointment_id=10,
        event_type="CANCELLED",
        channel="whatsapp",
        destination="whatsapp:+911234567890",
        status="PENDING",
    )

    sql = _sql_text(conn)
    assert "insert into appointment_notification_log" in sql
    assert "attempt_count, next_retry_at" in sql
    assert " 0, null" in sql


def test_upsert_delivery_status_uses_ist_for_both_timestamps() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conn = _FakeConn()
    repo._connect = lambda: conn  # type: ignore[method-assign]

    repo.upsert_delivery_status(
        provider="twilio",
        provider_message_sid="SM123",
        channel="whatsapp",
        message_status="DELIVERED",
    )

    sql = _sql_text(conn)
    assert "insert into message_delivery_status" in sql
    assert "update appointment_notification_log" in sql
    assert "convert_tz(utc_timestamp(), '+00:00', '+05:30')" in sql


def test_list_due_doctor_reminders_uses_ist_window_expression() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conn = _FakeConn(rows=[[]])
    repo._connect = lambda: conn  # type: ignore[method-assign]
    repo._use_appointment_mode = lambda: True  # type: ignore[method-assign]
    repo._appointment_table = lambda: "appointment"  # type: ignore[method-assign]
    repo._table_columns = lambda table_name: {"whatsapp_number", "telegram_chat_id"} if table_name == "doctors" else set()  # type: ignore[method-assign]

    repo.list_due_doctor_reminders(lookahead_minutes=60, admin_id=1)

    sql = _sql_text(conn)
    assert "convert_tz(utc_timestamp(), '+00:00', '+05:30')" in sql


def test_find_active_appointment_by_phone_filters_in_sql_not_python_scan() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    row = {
        "appointment_id": 1,
        "clinic_id": 1,
        "doctor_id": 1,
        "booking_number": 7,
        "clinic_name": "C",
        "slot_date": "2099-01-01",
        "slot_time": "10:00",
        "patient_phone": "919999999999",
    }
    conn = _FakeConn(rows=[[row]])
    repo._connect = lambda: conn  # type: ignore[method-assign]
    repo._use_appointment_mode = lambda: True  # type: ignore[method-assign]
    repo._appointment_table = lambda: "appointment"  # type: ignore[method-assign]

    out = repo.find_active_appointment_by_phone_number("9999999999", admin_id=1, doctor_id=1)
    sql = _sql_text(conn)

    assert out is not None
    assert "right(" in sql
    assert "coalesce(p.phone, '')" in sql
    assert "a.status in ('booked', 'pending', 'confirmed')" in sql


def test_list_active_appointments_by_phone_filters_in_sql_not_python_scan() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    row = {
        "appointment_id": 2,
        "clinic_id": 1,
        "doctor_id": 1,
        "clinic_name": "C",
        "slot_date": "2099-01-01",
        "slot_time": "10:00",
        "patient_phone": "919999999999",
    }
    conn = _FakeConn(rows=[[row]])
    repo._connect = lambda: conn  # type: ignore[method-assign]
    repo._use_appointment_mode = lambda: True  # type: ignore[method-assign]
    repo._appointment_table = lambda: "appointment"  # type: ignore[method-assign]

    out = repo.list_active_appointments_by_phone_number("9999999999", admin_id=1, doctor_id=1, limit=5)
    sql = _sql_text(conn)

    assert len(out) == 1
    assert "right(" in sql
    assert "coalesce(p.phone, '')" in sql


def test_default_doctor_id_by_phone_filters_in_sql_not_python_scan() -> None:
    repo = SchedulingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conn = _FakeConn(rows=[[{"doctor_id": 3}]])
    repo._connect = lambda: conn  # type: ignore[method-assign]

    doctor_id = repo.default_doctor_id_by_phone("9999999999", admin_id=1)
    sql = _sql_text(conn)

    assert doctor_id == 3
    assert "right(" in sql
    assert "limit 1" in sql


def test_booking_table_columns_are_cached_across_calls() -> None:
    repo = BookingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    conn = _FakeConn(rows=[[("telegram_chat_id",), ("phone",)]])
    repo._connect = lambda: conn  # type: ignore[method-assign]

    first = repo._table_columns("patients")
    second = repo._table_columns("patients")
    sql = _sql_text(conn)

    assert "telegram_chat_id" in first
    assert first == second
    assert sql.count("from information_schema.columns") == 1


def test_claim_cache_invalidation_rolls_back_when_event_build_fails() -> None:
    repo = SchedulingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    # queue_id None triggers int(None) error while constructing return object.
    broken_row = {
        "queue_id": None,
        "entity_type": "APPOINTMENT",
        "doctor_id": 1,
        "clinic_id": 1,
        "admin_id": 1,
        "slot_date": "2026-03-01",
        "slot_time": "10:00",
        "old_doctor_id": None,
        "old_clinic_id": None,
        "old_admin_id": None,
        "old_slot_date": None,
        "old_slot_time": None,
        "old_status": None,
        "new_status": None,
    }
    conn = _FakeConn(rows=[broken_row])
    repo._connect = lambda: conn  # type: ignore[method-assign]

    with pytest.raises(Exception):
        repo.claim_cache_invalidation_events(limit=1, worker_id="w1")

    assert conn.commits == 0
    assert conn.rollbacks >= 1


def test_claim_cache_invalidation_uses_ist_for_locked_at_and_stale_window() -> None:
    repo = SchedulingRepository(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
    row = {
        "queue_id": 5,
        "entity_type": "APPOINTMENT",
        "doctor_id": 1,
        "clinic_id": 1,
        "admin_id": 1,
        "slot_date": "2026-03-01",
        "slot_time": "10:00",
        "old_doctor_id": None,
        "old_clinic_id": None,
        "old_admin_id": None,
        "old_slot_date": None,
        "old_slot_time": None,
        "old_status": None,
        "new_status": None,
    }
    conn = _FakeConn(rows=[row])
    repo._connect = lambda: conn  # type: ignore[method-assign]

    events = repo.claim_cache_invalidation_events(limit=1, worker_id="w1")
    sql = _sql_text(conn)

    assert len(events) == 1
    assert "convert_tz(utc_timestamp(), '+00:00', '+05:30')" in sql
    assert conn.commits == 1

