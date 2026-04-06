import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_existing_booking_found_uses_patient_id_label(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.chat_phone_number = "telegram:999888777"
    fsm.booking_repository = Mock()

    monkeypatch.setattr(
        fsm,
        "_active_booking_rows_for_chat_phone",
        lambda: [
            {
                "appointment_id": 5,
                "booking_number": 5,
                "slot_date": "2026-03-13",
                "slot_time": "14:30:00",
                "clinic_name": "Health Plus Clinic",
                "clinic_id": 2,
                "doctor_id": 1,
            }
        ],
    )

    reply = fsm._existing_booking_entry_response()

    assert reply is not None
    assert "Patient ID: 5" in reply
    assert "Booking Number:" not in reply
