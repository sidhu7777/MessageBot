import sys
from datetime import datetime, time
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.qr.checkin_service import QrCheckinService
from src.repositories.scheduling_repository import SchedulingRepository
from src.timezone_utils import now_in_runtime_timezone


@dataclass
class _SaveResult:
    ok: bool
    message: str
    appointment_id: int | None = None
    queue_number: int | None = None


class _FakeBookingRepo:
    def __init__(self) -> None:
        self.active_rows = []
        self.saved_context = None
        self.saved_admin_id = None
        self.saved_doctor_id = None
        self.save_result = _SaveResult(ok=True, message="ok", appointment_id=77, queue_number=9)

    def _connect(self):
        raise RuntimeError("not expected in this test")

    def default_admin_id(self):
        return 1

    def list_active_appointments_by_phone_number(self, phone_number: str, admin_id: int, doctor_id: int, limit: int = 1):
        return list(self.active_rows)

    def save_confirmed_appointment(self, context, admin_id: int, doctor_id: int):
        self.saved_context = context
        self.saved_admin_id = admin_id
        self.saved_doctor_id = doctor_id
        return self.save_result


class _FakeSchedulingRepo:
    def __init__(self) -> None:
        self.dates = []
        self.times_by_date = {}
        self.date_calls = 0
        self.time_calls = []

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id: int, limit: int = 14):
        self.date_calls += 1
        return list(self.dates)

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id: int, limit: int = 60):
        self.time_calls.append((doctor_id, clinic_id, slot_date, admin_id, limit))
        return list(self.times_by_date.get(slot_date, []))


class _TestableQrService(QrCheckinService):
    def __init__(self, booking_repository, scheduling_repository):
        super().__init__(booking_repository, scheduling_repository)
        self.admin_id = 10
        self.doctor_name = "Sanjay"
        self.clinic_name = "Aditya"
        self.overflow_result = (88, 13, "2026-03-10", "10:05")
        self.test_schedules = []

    def _resolve_admin_id(self, doctor_id: int):
        return self.admin_id

    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int):
        return self.doctor_name, self.clinic_name

    def _active_booking(self, phone: str, admin_id: int, doctor_id: int, clinic_id: int):
        return self.booking_repository.active_rows[0] if self.booking_repository.active_rows else None

    def _book_confirmed_overflow(
        self,
        *,
        admin_id: int,
        doctor_id: int,
        clinic_id: int,
        patient_name: str,
        phone: str,
        target_session=None,
    ):
        return self.overflow_result

    def _today_schedules(self, *, doctor_id: int, clinic_id: int):
        return list(self.test_schedules)


def test_qr_service_active_booking_guard() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    br.active_rows = [
        {
            "appointment_id": 501,
            "booking_number": 15,
            "slot_date": "2026-03-10",
            "slot_time": "10:30",
        }
    ]
    svc = _TestableQrService(br, sr)

    result = svc.process_checkin(
        doctor_id=1,
        clinic_id=2,
        patient_name="Vineeth",
        phone="9876543210",
    )

    assert result.status == "active_booking"
    assert "already have an active booking" in result.message
    assert br.saved_context is None


def test_qr_service_books_first_available_slot() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    today = now_in_runtime_timezone().date().isoformat()
    sr.dates = [today]
    sr.times_by_date = {today: ["09:00", "09:15"]}
    svc = _TestableQrService(br, sr)

    result = svc.process_checkin(
        doctor_id=1,
        clinic_id=2,
        patient_name="Vineeth",
        phone="+91 98765 43210",
    )

    assert result.status == "booked"
    assert result.booking_id == 77
    assert br.saved_context is not None
    assert br.saved_context.appointment_date
    assert br.saved_context.appointment_time == "09:00"
    assert br.saved_context.phone_number == "919876543210"
    assert br.saved_admin_id == 10
    assert br.saved_doctor_id == 1
    assert sr.date_calls == 0
    assert len(sr.time_calls) == 1
    assert result.appointment_time == "9:00 AM"
    assert "Estimated Time: 9:00 AM." in result.message


def test_qr_service_overflow_when_no_slots() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    svc = _TestableQrService(br, sr)

    result = svc.process_checkin(
        doctor_id=1,
        clinic_id=2,
        patient_name="Aashi",
        phone="9999990000",
    )

    assert result.status == "booked"
    assert result.booking_id == 88
    assert result.appointment_time == "10:05"
    assert "Appointment ID: 13" in result.message
    assert br.saved_context is None


def test_qr_prefers_near_future_regular_slot_within_extension_window() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    today = "2026-03-10"
    sr.times_by_date = {today: ["10:30"]}
    svc = _TestableQrService(br, sr)
    svc.test_schedules = [(time(9, 0), time(10, 0), 5)]

    with patch("src.qr.checkin_service.now_in_runtime_timezone", return_value=datetime(2026, 3, 10, 10, 5)):
        result = svc.process_checkin(
            doctor_id=1,
            clinic_id=2,
            patient_name="Vineeth",
            phone="+91 98765 43210",
        )

    assert result.status == "booked"
    assert result.appointment_time == "10:30 AM"
    assert br.saved_context is not None


def test_qr_uses_recent_session_overflow_when_future_slot_is_beyond_extension_window() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    today = "2026-03-10"
    sr.times_by_date = {today: ["15:00"]}
    svc = _TestableQrService(br, sr)
    svc.test_schedules = [(time(9, 0), time(10, 0), 5), (time(15, 0), time(17, 0), 30)]

    with patch("src.qr.checkin_service.now_in_runtime_timezone", return_value=datetime(2026, 3, 10, 10, 5)):
        result = svc.process_checkin(
            doctor_id=1,
            clinic_id=2,
            patient_name="Vineeth",
            phone="+91 98765 43210",
        )

    assert result.status == "booked"
    assert result.booking_id == 88
    assert result.appointment_time == "10:05"
    assert br.saved_context is None


def test_qr_overflow_does_not_assign_past_time_after_recent_session_end() -> None:
    br = _FakeBookingRepo()
    sr = _FakeSchedulingRepo()
    today = "2026-03-10"
    sr.times_by_date = {today: ["15:00"]}
    svc = _TestableQrService(br, sr)
    svc.test_schedules = [(time(11, 0), time(11, 30), 5), (time(15, 0), time(17, 0), 30)]
    svc.overflow_result = (88, 4, today, "12:10")

    with patch("src.qr.checkin_service.now_in_runtime_timezone", return_value=datetime(2026, 3, 10, 12, 10)):
        result = svc.process_checkin(
            doctor_id=1,
            clinic_id=2,
            patient_name="Vineeth",
            phone="+91 98765 43210",
        )

    assert result.status == "booked"
    assert result.appointment_time == "12:10"
    assert br.saved_context is None


class _OverflowCursor:
    def __init__(self) -> None:
        self._one = None
        self._many = []
        self.lastrowid = 0
        self.inserted_start_time = None
        self.inserted_booking_id = None
        self.conflict_checks = []
        self._first_conflict_open = True

    def execute(self, query: str, params=None) -> None:
        q = " ".join(str(query or "").lower().split())
        self._one = None
        self._many = []

        if "select start_time, end_time, slot_duration from doctor_clinic_schedule" in q:
            self._many = [{"start_time": time(12, 0), "end_time": time(12, 55), "slot_duration": 5}]
            return
        if "select patient_id from patients" in q and "for update" in q:
            self._one = {"patient_id": 42}
            return
        if "select a.start_time from appointment a" in q and "for update" in q:
            self._many = []
            return
        if "select appointment_id, status from appointment" in q and "for update" in q:
            self.conflict_checks.append(tuple(params or ()))
            if self._first_conflict_open:
                self._first_conflict_open = False
                self._one = {"appointment_id": 700, "status": "BOOKED"}
            return
        if q.startswith("insert into appointment"):
            self.lastrowid = 901
            self.inserted_start_time = params[-3]
            self.inserted_booking_id = params[-1]
            return
        if q.startswith("update patients set booking_id"):
            return
        if q.startswith("update patients set"):
            return

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)

    def close(self) -> None:
        return


class _OverflowConn:
    def __init__(self) -> None:
        self.cursor_obj = _OverflowCursor()

    def cursor(self, dictionary: bool = False):
        return self.cursor_obj

    def start_transaction(self) -> None:
        return

    def commit(self) -> None:
        return

    def rollback(self) -> None:
        return

    def close(self) -> None:
        return


class _OverflowRepo(_FakeBookingRepo):
    def __init__(self) -> None:
        super().__init__()
        self.conn = _OverflowConn()

    def _connect(self):
        return self.conn

    def _appointment_table(self) -> str:
        return "appointment"

    def _use_appointment_mode(self) -> bool:
        return True

    def _table_columns(self, table_name: str) -> set[str]:
        if table_name == "patients":
            return {"patient_id", "full_name", "admin_id", "doctor_id", "phone", "booking_id"}
        if table_name == "appointment":
            return {"appointment_id", "patient_id", "doctor_id", "clinic_id", "admin_id", "status", "appointment_date", "start_time", "end_time", "booking_id"}
        return set()

    def _normalized_phone_sql_expr(self, column_name: str) -> str:
        return column_name

    def _normalize_schedules(self, schedules):
        return [(row["start_time"], row["end_time"], row["slot_duration"]) for row in schedules]


def test_qr_overflow_skips_doctor_time_conflict_across_clinics() -> None:
    br = _OverflowRepo()
    sr = _FakeSchedulingRepo()
    svc = QrCheckinService(br, sr)

    appointment_id, booking_id, overflow_date, overflow_time = svc._book_confirmed_overflow(
        admin_id=10,
        doctor_id=1,
        clinic_id=1,
        patient_name="Prem kumar",
        phone="8442626792",
    )

    assert appointment_id == 901
    assert booking_id == 12
    assert overflow_date
    assert overflow_time == "1:05 PM"
    assert len(br.conn.cursor_obj.conflict_checks) == 2
    assert br.conn.cursor_obj.inserted_start_time.strftime("%H:%M") == "13:05"
    assert br.conn.cursor_obj.inserted_booking_id == 12


class _SlotActiveCursor:
    def __init__(self) -> None:
        self._one = None

    def execute(self, query: str, params=None) -> None:
        q = " ".join(str(query or "").lower().split())
        self._one = None
        if "left join slots s on s.slot_id = a.slot_id" in q and "s.slot_date = %s" in q:
            self._one = {
                "appointment_id": 321,
                "booking_number": 12,
                "slot_date": "2026-03-10",
                "slot_time": "13:00",
            }

    def fetchone(self):
        return self._one

    def close(self) -> None:
        return


class _SlotActiveConn:
    def cursor(self, dictionary: bool = False):
        return _SlotActiveCursor()

    def close(self) -> None:
        return


class _SlotActiveRepo(_FakeBookingRepo):
    def _connect(self):
        return _SlotActiveConn()

    def _appointment_table(self) -> str:
        return "appointment"

    def _use_appointment_mode(self) -> bool:
        return False

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        return column_name == "booking_id"

    def _normalized_phone_sql_expr(self, column_name: str) -> str:
        return column_name


def test_qr_active_booking_detects_slot_based_same_day_booking() -> None:
    br = _SlotActiveRepo()
    sr = _FakeSchedulingRepo()
    svc = QrCheckinService(br, sr)

    result = svc.process_checkin(
        doctor_id=1,
        clinic_id=1,
        patient_name="Prem kumar",
        phone="8442626792",
    )

    assert result.status == "active_booking"
    assert "#12" in result.message
    assert result.appointment_time == "1:00 PM"


def test_today_visibility_hides_slots_that_already_started() -> None:
    fake_now = datetime.strptime("2026-03-10 11:11", "%Y-%m-%d %H:%M")
    with patch("src.repositories.scheduling_repository.now_in_runtime_timezone", return_value=fake_now):
        visible = SchedulingRepository._filter_runtime_visible_times(
            slot_date="2026-03-10",
            times=["11:00", "11:30", "12:00"],
            end_times_by_start={"11:00": "11:30", "11:30": "12:00", "12:00": "12:30"},
        )

    assert visible == ["11:30", "12:00"]
