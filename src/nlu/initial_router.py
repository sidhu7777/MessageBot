from src.llm.client import LLMClient
from src.llm.tasks import llm_route_intent_and_language
from src.nlu.language_detector import detect_language
from src.nlu.extractors import (
    has_booking_negative_signal,
    has_weak_booking_signal,
    is_availability_intent,
    is_booking_intent,
    is_greeting_intent,
)


def route_initial_decision(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    lower: str,
) -> tuple[str, str | None]:
    detected_language = detect_language(lower)
    normalized = lower.strip()
    if normalized in {"1", "option 1", "book now", "booking"} or normalized.startswith("1 "):
        return "BOOK_APPOINTMENT", detected_language
    if normalized in {"2", "option 2", "check availability"} or normalized.startswith("2 "):
        return "CHECK_AVAILABILITY", detected_language

    if has_booking_negative_signal(lower):
        decision, language = llm_route_intent_and_language(
            llm_client=llm_client,
            enable_llm_polish=enable_llm_polish,
            text=text,
            min_confidence=0.80,
        )
        if decision in {"CHECK_AVAILABILITY", "GREETING", "GENERAL_QUERY", "OTHER"}:
            return decision, language or detected_language
        return "GENERAL_QUERY", language or detected_language

    if is_availability_intent(lower):
        return "CHECK_AVAILABILITY", detected_language
    if is_booking_intent(lower):
        if has_weak_booking_signal(lower):
            decision, language = llm_route_intent_and_language(
                llm_client=llm_client,
                enable_llm_polish=enable_llm_polish,
                text=text,
                min_confidence=0.80,
            )
            if decision:
                return decision, language or detected_language
            return "GENERAL_QUERY", language or detected_language
        return "BOOK_APPOINTMENT", detected_language
    if is_greeting_intent(lower):
        return "GREETING", detected_language

    decision, language = llm_route_intent_and_language(
        llm_client=llm_client,
        enable_llm_polish=enable_llm_polish,
        text=text,
    )
    return (decision or "OTHER"), (language or detected_language)

