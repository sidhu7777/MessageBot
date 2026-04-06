import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_hindi_availability_reply_is_localized():
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.response_language = "hi"
    fsm.language_locked = True
    fsm.doctor_id = 1
    fsm.admin_id = 1
    fsm.scheduling_repository = Mock()
    fsm.scheduling_repository.get_availability_snapshot.return_value = {
        "clinics": [
            {"clinic_id": 2, "clinic_name": "Health Plus Clinic"},
        ],
        "dates_by_clinic": {"2": ["2026-03-13"]},
        "times_by_clinic_date": {"2|2026-03-13": ["09:00", "10:00"]},
    }

    reply = fsm._availability_reply("2026-03-13")

    assert "Doctor availability on" not in reply
    assert "2026-03-13 के लिए डॉक्टर की उपलब्धता:" in reply
    assert "1. अपॉइंटमेंट बुक करें" in reply
