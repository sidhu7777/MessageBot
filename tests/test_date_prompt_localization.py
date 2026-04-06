import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def _fsm(language: str) -> AppointmentFSM:
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.response_language = language
    fsm.language_locked = True
    return fsm


def test_booking_date_prompt_is_localized_for_all_languages():
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    en_reply = _fsm("en")._date_options_prompt([today, tomorrow])
    hi_reply = _fsm("hi")._date_options_prompt([today, tomorrow])
    hinglish_reply = _fsm("hinglish")._date_options_prompt([today, tomorrow])

    assert "Please choose appointment date:" in en_reply
    assert f"1. Today ({today})" in en_reply
    assert f"2. Tomorrow ({tomorrow})" in en_reply

    assert "कृपया अपॉइंटमेंट की तारीख चुनें:" in hi_reply
    assert f"1. आज ({today})" in hi_reply
    assert f"2. कल ({tomorrow})" in hi_reply

    assert "Please appointment date choose kariye:" in hinglish_reply
    assert f"1. Aaj ({today})" in hinglish_reply
    assert f"2. Kal ({tomorrow})" in hinglish_reply


def test_availability_date_prompt_is_localized_for_all_languages():
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    en_reply = _fsm("en")._availability_date_options_prompt([today, tomorrow])
    hi_reply = _fsm("hi")._availability_date_options_prompt([today, tomorrow])
    hinglish_reply = _fsm("hinglish")._availability_date_options_prompt([today, tomorrow])

    assert "Please choose a date to check availability:" in en_reply
    assert 'Press "0" to go back.' in en_reply

    assert "कृपया उपलब्धता जांचने के लिए तारीख चुनें:" in hi_reply
    assert 'Press "0" to go back.' not in hi_reply
    assert '0' in hi_reply

    assert "Please availability check karne ke liye date choose kariye:" in hinglish_reply
    assert 'Press "0" to go back.' in hinglish_reply
