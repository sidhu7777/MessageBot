import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.llm.client import LLMClient
from src.llm.tasks import llm_detect_abuse, llm_detect_language, llm_route_intent_and_language
from src.nlu.initial_router import route_initial_decision


TEST_TEXT = "मेरो को अपॉइंटम बुक करना है"


def test_live_llm_uses_env_selected_model_for_hindi_booking_probe():
    settings = load_settings()
    client = LLMClient(
        model=settings.llm_model,
        provider=settings.llm_provider,
        base_url=settings.ollama_base_url,
        timeout_seconds=120.0,
    )

    language = llm_detect_language(client, True, TEST_TEXT)
    abuse = llm_detect_abuse(client, True, TEST_TEXT)
    routed = llm_route_intent_and_language(client, True, TEST_TEXT)
    decision = route_initial_decision(client, True, TEST_TEXT, TEST_TEXT.lower())

    print(f"model={settings.llm_model}")
    print(f"language={language}")
    print(f"abuse={abuse}")
    print(f"llm_route={routed}")
    print(f"route_initial_decision={decision}")

    assert settings.llm_model
