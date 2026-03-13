from src.llm.client import LLMClient
from src.llm.tasks import llm_classify_initial_message
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
) -> tuple[str, str | None, bool]:
    detected_language = detect_language(lower)
    normalized = lower.strip()
    if normalized in {"1", "option 1", "book now", "booking"} or normalized.startswith("1 "):
        return "BOOK_APPOINTMENT", detected_language, False
    if normalized in {"2", "option 2", "check availability"} or normalized.startswith("2 "):
        return "CHECK_AVAILABILITY", detected_language, False

    def _llm_decision(min_confidence: float = 0.80) -> tuple[str, str | None, bool]:
        parsed = llm_classify_initial_message(
            llm_client=llm_client,
            enable_llm_polish=enable_llm_polish,
            text=text,
            min_confidence=min_confidence,
        )
        if not parsed:
            return "OTHER", detected_language, False
        decision = parsed.get("intent") or "OTHER"
        language = parsed.get("language") or detected_language
        abuse = bool(parsed.get("abuse"))
        return decision, language, abuse

    if has_booking_negative_signal(lower):
        decision, language, abuse = _llm_decision(min_confidence=0.80)
        if abuse:
            return "ABUSE", language or detected_language, True
        if decision in {"CHECK_AVAILABILITY", "GREETING", "GENERAL_QUERY", "OTHER"}:
            return decision, language or detected_language, False
        return "GENERAL_QUERY", language or detected_language, False

    if is_availability_intent(lower):
        return "CHECK_AVAILABILITY", detected_language, False
    if is_booking_intent(lower):
        if has_weak_booking_signal(lower):
            decision, language, abuse = _llm_decision(min_confidence=0.80)
            if abuse:
                return "ABUSE", language or detected_language, True
            if decision:
                return decision, language or detected_language, False
            return "GENERAL_QUERY", language or detected_language, False
        return "BOOK_APPOINTMENT", detected_language, False
    if is_greeting_intent(lower):
        return "GREETING", detected_language, False

    decision, language, abuse = _llm_decision(min_confidence=0.70)
    if abuse:
        return "ABUSE", language or detected_language, True
    return (decision or "OTHER"), (language or detected_language), False
