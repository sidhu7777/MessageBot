import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.messages.templates import get_message


def test_hindi_booking_start_flow_uses_hindi_booking_for_options(monkeypatch):
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
    assert "1. स्वयं के लिए" in second_reply
    assert "2. किसी अन्य व्यक्ति के लिए" in second_reply
    assert "Self" not in second_reply
    assert "Another person" not in second_reply


def test_hindi_self_booking_choice_stays_in_hindi(monkeypatch):
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.response_language = "hi"
    fsm.language_locked = True
    fsm.state = "ASK_BOOKING_FOR"
    fsm.known_patient_name = "प्रेम कुमार"
    fsm.chat_phone_number = "whatsapp:+919876543210"

    monkeypatch.setattr(fsm, "_auto_select_single_clinic_after_phone", lambda: None)
    monkeypatch.setattr(fsm, "_clinic_prompt", lambda: "कृपया क्लिनिक चुनें:")

    reply = fsm.handle("1")

    assert fsm.state == "ASK_CLINIC"
    assert "नोट किया गया। स्वयं के लिए बुकिंग की जा रही है।" in reply
    assert "धन्यवाद, प्रेम कुमार।" in reply
    assert "कृपया क्लिनिक चुनें:" in reply
    assert "Noted. Booking for self." not in reply


def test_hindi_option_templates_do_not_contain_english_labels():
    existing_booking = get_message(
        "hi",
        "existing_booking_found",
        appointment_id=15,
        clinic_name="Health Plus Clinic",
        appointment_date="2026-03-12",
        appointment_time="16:00",
    )
    confirm_prompt = get_message("hi", "confirm_prompt")
    change_prompt = get_message("hi", "ask_change_field")
    invalid_change = get_message("hi", "invalid_change_field")

    assert "रोगी आईडी: 15" in existing_booking
    assert "रद्द करें" in existing_booking
    assert "पुनर्निर्धारित करें" in existing_booking
    assert "Confirm" not in confirm_prompt
    assert "Change details" not in confirm_prompt
    assert "Go back" not in confirm_prompt
    assert "कृपया 1, 2, या 0 में उत्तर दें।" in confirm_prompt
    assert "Go back" not in change_prompt
    assert "Reply with 1-10 or 0." not in change_prompt
    assert "1. नाम" in change_prompt
    assert "2. संपर्क नंबर" in change_prompt
    assert "3. क्लिनिक" in change_prompt
    assert "4. तारीख" in change_prompt
    assert "5. समय" in change_prompt
    assert "मरीज़ का प्रकार" not in change_prompt
    assert "आयु" not in change_prompt
    assert "जेंडर" not in change_prompt
    assert "कृपया 1, 2, 3, 4, 5, या 0 में उत्तर दें।" in change_prompt
    assert "1-10" not in invalid_change
    assert "1-5" in invalid_change


def test_hindi_templates_do_not_use_remaining_english_booking_phrases():
    checks = [
        get_message("hi", "availability_ask"),
        get_message("hi", "availability_ask_date"),
        get_message("hi", "availability_noted", availability_doctor="शर्मा", availability_date="2026-03-12"),
        get_message("hi", "no_intent"),
        get_message("hi", "ask_phone"),
        get_message("hi", "invalid_phone_same_missing"),
        get_message("hi", "no_clinic_available_restart"),
        get_message("hi", "ask_date"),
        get_message("hi", "completed_hint"),
        get_message("hi", "ended"),
        get_message("hi", "cancelled_hint"),
    ]

    for text in checks:
        assert "book appointment" not in text
        assert "WhatsApp" not in text
        assert "tomorrow" not in text
        assert "Dr." not in text
