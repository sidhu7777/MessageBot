import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_init_unclear_input_uses_one_combined_llm_call(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=True, mixed_response_language="auto")

    classify_calls = {"count": 0}

    def fake_classify(*, llm_client, enable_llm_polish, text, min_confidence=0.70):
        classify_calls["count"] += 1
        return {
            "intent": "BOOK_APPOINTMENT",
            "language": "hi",
            "abuse": False,
            "confidence": 0.95,
        }

    def fail_detect_language(*args, **kwargs):
        raise AssertionError("separate llm_detect_language should not be called in INIT fallback")

    def fail_detect_abuse(*args, **kwargs):
        raise AssertionError("separate llm_detect_abuse should not be called in INIT fallback")

    monkeypatch.setattr("src.nlu.initial_router.llm_classify_initial_message", fake_classify)
    monkeypatch.setattr("src.llm.tasks.llm_detect_language", fail_detect_language)
    monkeypatch.setattr("src.llm.tasks.llm_detect_abuse", fail_detect_abuse)
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    reply = fsm.handle("some unclear opening")

    assert classify_calls["count"] == 1
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "यह अपॉइंटमेंट किसके लिए है?" in reply
