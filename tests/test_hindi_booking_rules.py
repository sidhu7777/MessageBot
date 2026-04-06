import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.nlu.extractors import is_booking_intent
from src.nlu.initial_router import route_initial_decision


def test_hindi_booking_rule_matches_typo_phrase():
    text = "मेरो को अपॉइंटम बुक करना है"
    lower = text.lower()

    assert is_booking_intent(lower) is True

    decision, language, abuse = route_initial_decision(
        llm_client=Mock(),
        enable_llm_polish=True,
        text=text,
        lower=lower,
    )

    assert decision == "BOOK_APPOINTMENT"
    assert language == "hi"
    assert abuse is False
