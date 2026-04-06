import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.messages.templates import get_message
from src.fsm.appointment_fsm import AppointmentFSM
from unittest.mock import Mock
from main import _timeout_message


def test_hindi_welcome_templates_exist():
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.response_language = "hi"
    fsm.known_patient_name = "हर्षित"
    fsm._doctor_display_name = lambda: "Sanjay Vinayak"

    welcome = fsm._welcome_greeting()
    booking_welcome = fsm._welcome_booking_start()

    assert "Welcome to Dr." not in welcome
    assert "डॉ. Sanjay Vinayak के क्लिनिक में आपका स्वागत है" in welcome
    assert "हर्षित" in welcome
    assert "डॉ. Sanjay Vinayak के क्लिनिक में आपका स्वागत है" in booking_welcome


def test_timeout_message_uses_hindi_template():
    text = _timeout_message("hi", "INIT")

    assert "We are facing a delay" not in text
    assert "Processing mein delay" not in text
    assert "देरी हो रही है" in text
