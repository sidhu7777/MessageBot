from types import SimpleNamespace

import src.repositories.booking_repository as booking_repository_module
from src.db.connection import MySQLConfig
from src.repositories.booking_repository import BookingRepository, BookingResult


def _make_repo() -> BookingRepository:
    return BookingRepository(
        MySQLConfig(
            user="u",
            password="p",
            host="localhost",
            port=3306,
            database="db",
        )
    )


def test_query_method_delegates_to_query_ops(monkeypatch) -> None:
    repo = _make_repo()
    captured = {}

    def fake(repo_arg, doctor_id, admin_id=None):
        captured["repo"] = repo_arg
        captured["doctor_id"] = doctor_id
        captured["admin_id"] = admin_id
        return "Dr. Delegated"

    monkeypatch.setattr(booking_repository_module, "_get_doctor_display_name", fake)

    result = repo.get_doctor_display_name(doctor_id=11, admin_id=4)

    assert result == "Dr. Delegated"
    assert captured["repo"] is repo
    assert captured["doctor_id"] == 11
    assert captured["admin_id"] == 4


def test_write_method_delegates_to_write_ops(monkeypatch) -> None:
    repo = _make_repo()
    captured = {}

    def fake(repo_arg, appointment_id, admin_id=None, cancelled_by="PATIENT"):
        captured["repo"] = repo_arg
        captured["appointment_id"] = appointment_id
        captured["admin_id"] = admin_id
        captured["cancelled_by"] = cancelled_by
        return True

    monkeypatch.setattr(booking_repository_module, "_cancel_appointment", fake)

    ok = repo.cancel_appointment(appointment_id=99, admin_id=7, cancelled_by="ADMIN")

    assert ok is True
    assert captured["repo"] is repo
    assert captured["appointment_id"] == 99
    assert captured["admin_id"] == 7
    assert captured["cancelled_by"] == "ADMIN"


def test_save_confirmed_appointment_delegates_to_write_ops(monkeypatch) -> None:
    repo = _make_repo()
    captured = {}
    context = SimpleNamespace(patient_name="X")

    def fake(repo_arg, context, admin_id=None, doctor_id=None):
        captured["repo"] = repo_arg
        captured["context"] = context
        captured["admin_id"] = admin_id
        captured["doctor_id"] = doctor_id
        return BookingResult(True, "ok", appointment_id=5, queue_number=2)

    monkeypatch.setattr(booking_repository_module, "_save_confirmed_appointment", fake)

    result = repo.save_confirmed_appointment(context=context, admin_id=10, doctor_id=2)

    assert result.ok is True
    assert result.appointment_id == 5
    assert captured["repo"] is repo
    assert captured["context"] is context
    assert captured["admin_id"] == 10
    assert captured["doctor_id"] == 2
