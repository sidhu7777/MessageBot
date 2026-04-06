import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from src.qr.checkin_service import QrCheckinResult


class _FakeQrService:
    def __init__(self) -> None:
        self.calls = []

    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int):
        return "Sanjay", "Aditya"

    def process_checkin(self, *, doctor_id: int, clinic_id: int, patient_name: str, phone: str):
        self.calls.append((doctor_id, clinic_id, patient_name, phone))
        return QrCheckinResult(
            status="booked",
            message="Booking confirmed. Your booking number is 12.",
            booking_id=12,
            appointment_date="2026-03-10",
            appointment_time="10:00",
            clinic_name="Aditya",
            doctor_name="Sanjay",
        )


class _FakeQrErrorService:
    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int):
        return "Sanjay", "Aditya"

    def process_checkin(self, *, doctor_id: int, clinic_id: int, patient_name: str, phone: str):
        return QrCheckinResult(
            status="error",
            message="Doctor schedule is not configured for this clinic.",
            clinic_name="Aditya",
            doctor_name="Sanjay",
        )


class _FakeQrLookupErrorService:
    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int):
        raise RuntimeError("db timeout")

    def process_checkin(self, *, doctor_id: int, clinic_id: int, patient_name: str, phone: str):
        return QrCheckinResult(status="booked", message="ok", booking_id=1)


def test_qr_checkin_page_and_submit(monkeypatch) -> None:
    fake = _FakeQrService()
    monkeypatch.setattr(app_main, "qr_checkin_service", fake, raising=True)

    with TestClient(app_main.app) as client:
        page = client.get("/qr/checkin", params={"doctor_id": 1, "clinic_id": 2})
        assert page.status_code == 200
        assert "Select language" in page.text
        assert "Welcome to Dr. Sanjay clinic" in page.text

        resp = client.post(
            "/qr/checkin/submit",
            json={
                "doctor_id": 1,
                "clinic_id": 2,
                "patient_name": "Vineeth",
                "phone_number": "9876543210",
                "language": "en",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "booked"
        assert data["booking_id"] == 12
        assert fake.calls == [(1, 2, "Vineeth", "9876543210")]


def test_qr_checkin_submit_returns_error_detail(monkeypatch) -> None:
    fake = _FakeQrErrorService()
    monkeypatch.setattr(app_main, "qr_checkin_service", fake, raising=True)

    with TestClient(app_main.app) as client:
        resp = client.post(
            "/qr/checkin/submit",
            json={
                "doctor_id": 1,
                "clinic_id": 2,
                "patient_name": "Vineeth",
                "phone_number": "9876543210",
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["status"] == "error"
        assert data["detail"] == "Doctor schedule is not configured for this clinic."


def test_qr_checkin_page_falls_back_when_name_lookup_fails(monkeypatch) -> None:
    fake = _FakeQrLookupErrorService()
    monkeypatch.setattr(app_main, "qr_checkin_service", fake, raising=True)

    with TestClient(app_main.app) as client:
        page = client.get("/qr/checkin", params={"doctor_id": 1, "clinic_id": 2})
        assert page.status_code == 200
        assert "Welcome to Dr. Doctor clinic" in page.text
        assert "Clinic: Clinic" in page.text
