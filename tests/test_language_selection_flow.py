import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_direct_booking_goes_to_language_selection_then_booking_flow(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("BOOK_APPOINTMENT", "hi", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    first_reply = fsm.handle("मुझे अपॉइंटमेंट बुक करनी है")

    assert fsm.state == "ASK_LANGUAGE"
    assert "Please choose your language:" in first_reply

    second_reply = fsm.handle("2")

    assert fsm.state == "ASK_BOOKING_FOR"
    assert "यह अपॉइंटमेंट किसके लिए है?" in second_reply
    assert "डॉ. Doctor" not in second_reply
    assert "मैं आज आपकी किस प्रकार मदद कर सकता हूँ?" not in second_reply


def test_greeting_goes_to_language_selection_then_options_only(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("GREETING", "hi", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    fsm.handle("नमस्ते")
    second_reply = fsm.handle("2")

    assert fsm.state == "INIT"
    assert "कृपया एक विकल्प चुनें:" in second_reply
    assert "डॉ. Doctor" not in second_reply


def test_after_language_selection_init_menu_does_not_reopen_language_step(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("GREETING", "hi", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    first_reply = fsm.handle("Hello")
    second_reply = fsm.handle("2")
    third_reply = fsm.handle("1")

    assert "Please choose your language:" in first_reply
    assert "कृपया एक विकल्प चुनें:" in second_reply
    assert fsm.state == "ASK_BOOKING_FOR"
    assert "यह अपॉइंटमेंट किसके लिए है?" in third_reply
    assert "Please choose your language:" not in third_reply


def test_english_main_menu_includes_back_and_zero_reopens_language_selection(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("GREETING", "en", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    fsm.handle("Hello")
    menu_reply = fsm.handle("1")

    assert fsm.state == "INIT"
    assert "0. Go back" in menu_reply
    assert "Reply with 1, 2, or 0." in menu_reply

    back_reply = fsm.handle("0")

    assert fsm.state == "ASK_LANGUAGE"
    assert "Please choose your language:" in back_reply


def test_back_to_init_does_not_repeat_welcome_before_language_screen(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")

    monkeypatch.setattr(
        "src.fsm.handlers.init_availability.route_initial_decision",
        lambda **kwargs: ("GREETING", "en", False),
    )
    monkeypatch.setattr(fsm, "_doctor_display_name", lambda: "Doctor")
    monkeypatch.setattr(fsm, "_existing_booking_entry_response", lambda: None)

    fsm.handle("Hello")
    fsm.handle("1")
    fsm.handle("1")

    init_reply = fsm.handle("0")

    assert fsm.state == "INIT"
    assert "Welcome to Dr." not in init_reply
    assert "Please choose one option:" in init_reply

    language_reply = fsm.handle("0")

    assert fsm.state == "ASK_LANGUAGE"
    assert "Please choose your language:" in language_reply
