import sys
from datetime import time
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.qr.checkin_service import QrCheckinService


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

    def _resolve_admin_id(self, doctor_id: int):
        return self.admin_id

    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int):
        return self.doctor_name, self.clinic_name

    def _active_booking(self, phone: str, admin_id: int, doctor_id: int, clinic_id: int):
        return self.booking_repository.active_rows[0] if self.booking_repository.active_rows else None

    def _book_confirmed_overflow(self, *, admin_id: int, doctor_id: int, clinic_id: int, patient_name: str, phone: str):
        return self.overflow_result


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
    sr.dates = ["2026-03-10"]
    sr.times_by_date = {"2026-03-10": ["09:00", "09:15"]}
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
    assert br.saved_context.appointment_date == "2026-03-10"
    assert br.saved_context.appointment_time == "09:00"
    assert br.saved_context.phone_number == "919876543210"
    assert br.saved_admin_id == 10
    assert br.saved_doctor_id == 1
    assert sr.date_calls == 0
    assert len(sr.time_calls) == 1


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
    assert "Patient ID: 13" in result.message
    assert br.saved_context is None


class _OverflowCursor:
    def __init__(self) -> None:
        self._one = None
        self._many = []
        self.lastrowid = 0
        self.inserted_start_time = None
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
        if "select coalesce(max(p.booking_id), 0) as max_booking_id" in q:
            self._one = {"max_booking_id": 12}
            return
        if "select appointment_id, status from appointment" in q and "for update" in q:
            self.conflict_checks.append(tuple(params or ()))
            if self._first_conflict_open:
                self._first_conflict_open = False
                self._one = {"appointment_id": 700, "status": "BOOKED"}
            return
        if q.startswith("insert into appointment"):
            self.lastrowid = 901
            self.inserted_start_time = params[-2]
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
    assert booking_id == 14
    assert overflow_date
    assert overflow_time == "13:10"
    assert len(br.conn.cursor_obj.conflict_checks) == 2
    assert br.conn.cursor_obj.inserted_start_time.strftime("%H:%M") == "13:10"


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
    assert result.appointment_time == "13:00"
