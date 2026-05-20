from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.whatsapp_web_routes as web_routes
from src.api.whatsapp_web_routes import register_whatsapp_web_routes


class _FakeCursor:
    def __init__(self):
        self._row = None

    def execute(self, query, params=()):
        if "FROM doctors" in query:
            self._row = {"admin_id": 1}
            return
        if "UPPER(COALESCE(p.profile_type" in query:
            self._row = {"full_name": "Vineeth Raja"}
            return
        if "FROM appointment" in query and "a.appointment_date = %s" in query:
            self._row = {
                "appointment_id": 25,
                "clinic_id": 7,
                "doctor_id": 3,
                "booking_number": 25,
                "clinic_name": "Main Clinic",
                "slot_date": "2026-04-29",
                "slot_time": "14:00",
                "patient_name": "Vineeth Raja",
            }
            return
        self._row = None

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _FakeConnection:
    def cursor(self, dictionary=False):
        return _FakeCursor()

    def close(self):
        pass


class _FakeBookingRepository:
    def _connect(self):
        return _FakeConnection()

    def _normalize_phone(self, value):
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    def default_admin_id(self):
        return 1

    def _appointment_table(self):
        return "appointment"

    def _column_exists(self, table, column):
        return column == "booking_id"

    def _normalized_phone_sql_expr(self, column):
        return f"REGEXP_REPLACE({column}, '[^0-9]', '')"

    def list_active_appointments_by_phone_number(self, *args, **kwargs):
        return []


def test_lookup_returns_same_day_identity_when_active_list_skips_it(monkeypatch):
    app = FastAPI()
    monkeypatch.setattr(
        web_routes,
        "now_in_runtime_timezone",
        lambda: datetime(2026, 4, 29, 14, 35),
    )
    register_whatsapp_web_routes(
        app,
        booking_repository=_FakeBookingRepository(),
        scheduling_repository=object(),
        logger=None,
    )

    client = TestClient(app)
    response = client.post(
        "/whatsapp/web/lookup",
        json={
            "doctor_id": 3,
            "patient_name": "Vineeth Raja",
            "phone_number": "9392569600",
            "booking_for_self": True,
            "detected_language": "en",
        },
    )

    assert response.status_code == 200
    appointments = response.json()["appointments"]
    assert appointments == [
        {
            "appointment_id": 25,
            "booking_number": 25,
            "clinic_id": 7,
            "clinic_name": "Main Clinic",
            "patient_name": "Vineeth Raja",
            "slot_date": "2026-04-29",
            "slot_time": "2:00 PM",
            "doctor_id": 3,
        }
    ]
