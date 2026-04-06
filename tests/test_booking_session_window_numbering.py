import sys
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.connection import MySQLConfig
from src.repositories.booking_repository import BookingRepository


class _Ctx:
    def __init__(self, *, appointment_time: str) -> None:
        self.patient_name = "Test Patient"
        self.appointment_date = date.today().isoformat()
        self.appointment_time = appointment_time
        self.clinic_id = "2"
        self.phone_number = "9876543210"
        self.age = 30
        self.gender = "Male"
        self.patient_type = "New"
        self.reason = "General"
        self.appointment_mode = None
        self.symptoms = None
        self.chat_user_id = None
        self.booking_for_self = True


class _FakeCursor:
    def __init__(self, *, schedules: list[dict]) -> None:
        self._schedules = schedules
        self._one = None
        self._many = []
        self.lastrowid = 0

    def execute(self, query: str, params=None) -> None:
        q = " ".join(str(query or "").lower().split())
        self._one = None
        self._many = []

        if "select patient_id from patients where full_name" in q:
            return
        if q.startswith("insert into patients"):
            self.lastrowid = 11
            return
        if "select dcs.doctor_id from doctor_clinic_schedule dcs" in q:
            self._one = {"doctor_id": 1}
            return
        if "select start_time, end_time, slot_duration from doctor_clinic_schedule" in q:
            self._many = list(self._schedules)
            return
        if "select appointment_id from appointment where patient_id" in q:
            return
        if "select appointment_id, status from appointment where doctor_id" in q:
            return
        if q.startswith("insert into appointment"):
            self.lastrowid = 501
            return
        if "select appointment_id, clinic_id, doctor_id, patient_id from appointment" in q:
            self._one = {
                "appointment_id": 700,
                "clinic_id": 2,
                "doctor_id": 1,
                "patient_id": 11,
            }
            return
        if "select appointment_id from appointment where doctor_id" in q and "appointment_id <>" in q:
            return
        # UPDATE/other no-op for this probe.

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)

    def close(self) -> None:
        return


class _FakeConn:
    def __init__(self, *, schedules: list[dict]) -> None:
        self._schedules = schedules

    def cursor(self, dictionary: bool = False):
        return _FakeCursor(schedules=self._schedules)

    def start_transaction(self) -> None:
        return

    def commit(self) -> None:
        return

    def rollback(self) -> None:
        return

    def close(self) -> None:
        return


class _ProbeRepo(BookingRepository):
    def __init__(self, *, schedules: list[dict]) -> None:
        super().__init__(MySQLConfig(user="u", password="p", host="h", port=3306, database="d"))
        self._probe_schedules = schedules

    def _connect(self):
        return _FakeConn(schedules=self._probe_schedules)

    def default_admin_id(self):
        return 1

    def _appointment_table(self) -> str:
        return "appointment"

    def _use_appointment_mode(self) -> bool:
        return True

    def _table_columns(self, table_name: str) -> set[str]:
        if table_name == "patients":
            return {
                "patient_id",
                "full_name",
                "admin_id",
                "phone",
                "age",
                "gender",
                "patient_type",
                "reason",
                "booking_id",
                "doctor_id",
            }
        return set()

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        if table_name == "patients" and column_name == "booking_id":
            return True
        if table_name == "appointment" and column_name == "notify_telegram_chat_id":
            return False
        if table_name == "appointment" and column_name == "rescheduled_by":
            return False
        return False

    def get_daily_queue_number(self, appointment_id: int):
        return 999


def _split_session_schedules() -> list[dict]:
    # Same date: two independent windows (09:00-10:00 and 14:00-16:00), 5-minute slots.
    return [
        {"start_time": time(9, 0), "end_time": time(10, 0), "slot_duration": 5},
        {"start_time": time(14, 0), "end_time": time(16, 0), "slot_duration": 5},
    ]


def test_save_confirmed_appointment_uses_session_window_numbering():
    repo = _ProbeRepo(schedules=_split_session_schedules())

    r_morning = repo.save_confirmed_appointment(_Ctx(appointment_time="09:30"), admin_id=1, doctor_id=1)
    assert r_morning.ok is True
    assert r_morning.queue_number == 7

    r_afternoon = repo.save_confirmed_appointment(_Ctx(appointment_time="14:00"), admin_id=1, doctor_id=1)
    assert r_afternoon.ok is True
    # Session-window numbering: first slot of afternoon window is 1 (not cumulative 13).
    assert r_afternoon.queue_number == 1


def test_reschedule_appointment_uses_session_window_numbering():
    repo = _ProbeRepo(schedules=_split_session_schedules())

    r_morning = repo.reschedule_appointment_same_clinic(
        appointment_id=700,
        new_date=date.today().isoformat(),
        new_time="09:30",
        new_clinic_id=2,
        admin_id=1,
    )
    assert r_morning.ok is True
    assert r_morning.queue_number == 7

    r_afternoon = repo.reschedule_appointment_same_clinic(
        appointment_id=700,
        new_date=date.today().isoformat(),
        new_time="14:00",
        new_clinic_id=2,
        admin_id=1,
    )
    assert r_afternoon.ok is True
    assert r_afternoon.queue_number == 1
