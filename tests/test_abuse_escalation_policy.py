import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM


class _StubLLM:
    def generate(self, system: str, user: str) -> str:
        return "{}"


def _new_fsm() -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=_StubLLM(),
        enable_llm_polish=False,
        mixed_response_language="auto",
    )
    fsm.chat_phone_number = "whatsapp:+919392569600"
    return fsm


def test_abuse_first_warn_second_final_then_silent() -> None:
    fsm = _new_fsm()

    r1 = fsm.handle("fuck you")
    assert "respectful language" in r1.lower()
    assert fsm.context.abuse_blocked is False

    r2 = fsm.handle("idiot")
    assert "contact the clinic directly" in r2.lower()
    assert fsm.context.abuse_blocked is True

    r3 = fsm.handle("I need to book appointment")
    assert r3 == ""

