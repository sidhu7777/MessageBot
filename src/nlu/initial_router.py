from src.llm.client import LLMClient
from src.llm.tasks import llm_route_intent
from src.nlu.extractors import is_availability_intent, is_booking_intent, is_greeting_intent


def route_initial_intent(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    lower: str,
) -> str:
    normalized = lower.strip()
    if normalized in {"1", "option 1", "book now", "booking"} or normalized.startswith("1 "):
        return "BOOK_APPOINTMENT"
    if normalized in {"2", "option 2", "check availability"} or normalized.startswith("2 "):
        return "CHECK_AVAILABILITY"

    if is_availability_intent(lower):
        return "CHECK_AVAILABILITY"
    if is_booking_intent(lower):
        return "BOOK_APPOINTMENT"
    if is_greeting_intent(lower):
        return "GREETING"
    decision = llm_route_intent(
        llm_client=llm_client,
        enable_llm_polish=enable_llm_polish,
        text=text,
    )
    if decision:
        return decision
    return "OTHER"
