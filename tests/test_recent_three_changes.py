from dataclasses import dataclass
from types import SimpleNamespace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from src.fsm.appointment_fsm import AppointmentFSM
from src.repositories.booking_repository import BookingRepository


class _StubLLM:
    def generate(self, system: str, user: str) -> str:
        s = (system or "").lower()
        # Return strong prefill payload; INIT should still stay rule-based now.
        if "booking prefill fields from first user message" in s:
            return (
                '{"patient_name":"Vineeth","appointment_date":"2026-02-24",'
                '"appointment_time":"17:00","clinic_name":"City Care Clinic","booking_for":"self"}'
            )
        return "BOOK_APPOINTMENT"


@dataclass
class _Clinic:
    clinic_id: int
    clinic_name: str
    location: str
    today_slots: int


class _FSMBookingRepo:
    def __init__(self, with_active: bool = True) -> None:
        self.with_active = with_active

    def default_admin_id(self):
        return 1

    def get_doctor_display_name(self, doctor_id, admin_id=None):
        return "Sanjay Vinayak"

    def list_active_appointments_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None, limit: int = 10):
        if not self.with_active:
            return []
        # Two active rows to trigger max-active branch.
        return [
            {
                "appointment_id": 1,
                "clinic_id": 11,
                "clinic_name": "City Care Clinic",
                "doctor_id": 1,
                "slot_date": "2026-02-24",
                "slot_time": "10:00",
                "status": "BOOKED",
            },
            {
                "appointment_id": 2,
                "clinic_id": 12,
                "clinic_name": "City Care Clinic 2",
                "doctor_id": 1,
                "slot_date": "2026-02-24",
                "slot_time": "11:00",
                "status": "BOOKED",
            },
        ]

    def find_patient_name_by_phone_number(self, phone_number: str, admin_id=None, doctor_id=None):
        return "Vineeth"


class _FSMSchedulingRepo:
    def default_doctor_id(self, admin_id=None):
        return 1

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [_Clinic(1, "City Care Clinic", "Delhi", 3)][:limit]

    def doctor_accept_days(self, doctor_id: int, admin_id=None):
        return 2


def _new_fsm(*, with_active: bool = True) -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=_StubLLM(),
        enable_llm_polish=True,
        booking_repository=_FSMBookingRepo(with_active=with_active),
        scheduling_repository=_FSMSchedulingRepo(),
        mixed_response_language="auto",
    )
    fsm.chat_phone_number = "whatsapp:+919392569600"
    return fsm


def test_init_book_intent_stays_rule_based_to_ask_booking_for() -> None:
    fsm = _new_fsm(with_active=False)
    reply = fsm.handle("Hi, my name is Vineeth and book appointment today at 5 PM")
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "who is this appointment for" in reply.lower()
    assert "please confirm your appointment details" not in reply.lower()


def test_max_active_menu_uses_zero_for_go_back() -> None:
    fsm = _new_fsm()
    fsm.state = "ASK_EXISTING_BOOKING_ACTION"

    reply = fsm.handle("4")
    assert fsm.state == "ASK_MAX_ACTIVE_BOOKINGS_ACTION"
    assert "1. cancel" in reply.lower()
    assert "2. reschedule" in reply.lower()
    assert 'press "0" to go back' in reply.lower()

    back = fsm.handle("0")
    assert fsm.state == "ASK_EXISTING_BOOKING_ACTION"
    assert "please choose again" in back.lower()


class _FakeCursor:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple | None]] = []
        self._current = ""
        self._current_params: tuple | None = None
        self.existing_chat_patient_id: int | None = None
        self.existing_chat_admin_id: int | None = None
        self.raise_duplicate_on_insert: bool = False

    def execute(self, sql: str, params=None):
        self._current = sql
        self._current_params = tuple(params) if params is not None else None
        self.queries.append((sql, self._current_params))

        if "FROM INFORMATION_SCHEMA.COLUMNS" in sql and "TABLE_NAME = 'patients'" in sql:
            return
        if "SELECT patient_id" in sql and "FROM patients" in sql:
            return
        if "INSERT INTO patients" in sql:
            if self.raise_duplicate_on_insert:
                raise Exception("1062 (23000): Duplicate entry '8299824956' for key 'patients_telegram_chat_id_key'")
            return
        return

    def fetchall(self):
        if "INFORMATION_SCHEMA.COLUMNS" in self._current:
            return [
                {"COLUMN_NAME": "patient_id"},
                {"COLUMN_NAME": "full_name"},
                {"COLUMN_NAME": "admin_id"},
                {"COLUMN_NAME": "phone"},
                {"COLUMN_NAME": "telegram_chat_id"},
            ]
        return []

    def fetchone(self):
        if "TRIM(COALESCE(" in self._current and "FROM patients" in self._current:
            if self.existing_chat_patient_id is not None:
                # Same-admin recovery query should return row only when admin matches.
                if "WHERE admin_id = %s" in self._current:
                    expected_admin = None
                    if self._current_params:
                        expected_admin = self._current_params[0]
                    if (
                        self.existing_chat_admin_id is not None
                        and expected_admin is not None
                        and int(self.existing_chat_admin_id) != int(expected_admin)
                    ):
                        return None
                return {
                    "patient_id": self.existing_chat_patient_id,
                    "admin_id": self.existing_chat_admin_id,
                }
            return None
        if "SELECT patient_id" in self._current:
            return None
        return None

    @property
    def lastrowid(self):
        return 999

    def close(self):
        return None


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def start_transaction(self):
        return None

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def rollback(self):
        return None

    def commit(self):
        return None

    def close(self):
        return None


class _RepoForSqlCheck(BookingRepository):
    def __init__(self):
        super().__init__(config=SimpleNamespace())
        self.conn = _FakeConn()

    def _connect(self):
        return self.conn

    def _appointment_table(self) -> str:
        return "appointment"

    def _use_appointment_mode(self) -> bool:
        return True


def test_other_person_patient_upsert_does_not_use_chat_id_and_uses_phone_in_lookup() -> None:
    repo = _RepoForSqlCheck()
    ctx = SimpleNamespace(
        patient_name="Ravi",
        appointment_date="2026-02-24",
        appointment_time="10:00",
        clinic_id="1",
        phone_number="9292929282",
        chat_user_id="8299824956",
        booking_for_self=False,
        age=None,
        gender=None,
        patient_type=None,
        reason=None,
        appointment_mode=None,
        symptoms=None,
    )

    repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)

    sql_texts = [q[0] for q in repo.conn.cursor_obj.queries]
    params = [q[1] for q in repo.conn.cursor_obj.queries if q[1] is not None]
    lookup_sql = next(s for s in sql_texts if "SELECT patient_id" in s and "FROM patients" in s)
    assert "COALESCE(phone,''" in lookup_sql
    assert any(p and p[-1] == "%9292929282" for p in params if len(p) >= 3)

    insert_sql = next((s for s in sql_texts if "INSERT INTO patients" in s), "")
    if insert_sql:
        assert "telegram_chat_id" not in insert_sql


def test_self_booking_reuses_existing_chat_id_patient_instead_of_insert() -> None:
    repo = _RepoForSqlCheck()
    repo.conn.cursor_obj.existing_chat_patient_id = 5
    ctx = SimpleNamespace(
        patient_name="New Name",
        appointment_date="2026-02-24",
        appointment_time="10:00",
        clinic_id="1",
        phone_number="9000000000",
        chat_user_id="8299824956",
        booking_for_self=True,
        age=None,
        gender=None,
        patient_type=None,
        reason=None,
        appointment_mode=None,
        symptoms=None,
    )

    repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)

    sql_texts = [q[0] for q in repo.conn.cursor_obj.queries]
    assert any("FROM patients" in s and "COALESCE(" in s for s in sql_texts)
    assert not any("INSERT INTO patients" in s for s in sql_texts)
    assert any("UPDATE patients" in s for s in sql_texts)


def test_self_booking_duplicate_chat_id_conflict_across_admin_returns_failure() -> None:
    repo = _RepoForSqlCheck()
    repo.conn.cursor_obj.existing_chat_patient_id = 5
    repo.conn.cursor_obj.existing_chat_admin_id = 2
    repo.conn.cursor_obj.raise_duplicate_on_insert = True
    ctx = SimpleNamespace(
        patient_name="New Name",
        appointment_date="2026-02-24",
        appointment_time="10:00",
        clinic_id="1",
        phone_number="9000000000",
        chat_user_id="8299824956",
        booking_for_self=True,
        age=None,
        gender=None,
        patient_type=None,
        reason=None,
        appointment_mode=None,
        symptoms=None,
    )

    result = repo.save_confirmed_appointment(ctx, admin_id=1, doctor_id=1)
    assert result.ok is False
    assert "different admin profile" in result.message.lower()


def test_actionable_booking_row_skips_past_slots() -> None:
    # Past date should never be treated as active.
    assert BookingRepository._is_actionable_booking_row("2000-01-01", "10:00") is False
