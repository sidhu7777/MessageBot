import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


def test_hindi_time_period_prompt_is_fully_localized():
    fsm = AppointmentFSM(llm_client=Mock(), enable_llm_polish=False, mixed_response_language="auto")
    fsm.response_language = "hi"
    fsm.time_hour_options_cache = ["09", "12", "17"]

    reply = fsm._time_period_prompt()

    assert "कृपया पसंदीदा समय अवधि चुनें:" in reply
    assert "1. सुबह" in reply
    assert "2. दोपहर" in reply
    assert "3. शाम" in reply
    assert "Morning" not in reply
    assert "Afternoon" not in reply
    assert "Evening" not in reply
