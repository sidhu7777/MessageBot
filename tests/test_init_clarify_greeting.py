import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_init_clarify_includes_welcome_for_unclear_input(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("OTHER", "en", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")

    reply = fsm.handle("Helllo")

    assert fsm.state == "ASK_LANGUAGE"
    assert "Hello / नमस्ते" in reply
    assert "Welcome to Dr. Doctor clinic." in reply
    assert "Please choose your language:" in reply
    assert "1. English" in reply
    assert "2. हिंदी" in reply
    assert "3. Hinglish" in reply


def test_init_clarify_includes_known_patient_name(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.known_patient_name = "Vineeth Raja"

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("OTHER", "en", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")

    reply = fsm.handle("unclear opening")

    assert fsm.state == "ASK_LANGUAGE"
    assert "Welcome to Dr. Doctor clinic, Vineeth Raja." in reply
    assert "Please choose your language:" in reply
