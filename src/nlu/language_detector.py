import re
from typing import Optional

from src.llm.client import LLMClient
from src.llm.tasks import llm_detect_language

def update_response_language(
    lower: str,
    mixed_response_language: str,
    response_language: str,
    language_locked: bool,
    language_turn_count: int,
    llm_client: Optional[LLMClient] = None,
    enable_llm_fallback: bool = False,
) -> tuple[str, bool, int]:
    if "language english" in lower or "speak english" in lower:
        return "en", True, language_turn_count
    if "language hindi" in lower or "speak hindi" in lower:
        return "hi", True, language_turn_count
    if "language hinglish" in lower or "speak hinglish" in lower or "speak in hinglish" in lower:
        return "hinglish", True, language_turn_count

    mode = mixed_response_language.lower()
    if mode in {"en", "hi", "hinglish"}:
        return mode, True, language_turn_count

    if language_locked:
        return response_language, language_locked, language_turn_count

    language_turn_count += 1
    detected = detect_language_with_fallback(
        lower=lower,
        llm_client=llm_client,
        enable_llm_fallback=enable_llm_fallback,
    )
    if detected:
        response_language = detected
    if language_turn_count >= 2 and response_language in {"en", "hi", "hinglish"}:
        language_locked = True
    return response_language, language_locked, language_turn_count


def detect_language(lower: str) -> Optional[str]:
    if re.search(r"[\u0900-\u097F]", lower):
        return "hi"

    hi_tokens = {
        "namaste", "mera", "mujhe", "krna", "karna", "karunga", "karo", "kya",
        "kab", "batao", "dikhao", "chahiye", "hai", "haan", "nahi", "naam",
        "umar", "samay", "kal", "aaj", "ki", "ka",
    }
    en_tokens = {
        "hi", "hello", "hey", "there", "book", "booking", "appointment", "name",
        "old", "new", "age", "date", "time", "reason", "symptoms", "confirm",
        "yes", "no", "available", "availability", "doctor", "tomorrow", "checkup",
        "consult", "visit", "schedule", "slot", "free",
    }

    words = re.findall(r"[a-zA-Z]+", lower)
    if not words:
        return None

    hi_score = sum(1 for word in words if word in hi_tokens)
    en_score = sum(1 for word in words if word in en_tokens)

    if hi_score > 0 and en_score > 0:
        return "hinglish"
    if en_score > 0 and hi_score == 0:
        return "en"
    if hi_score > 0 and en_score == 0:
        return "hinglish"
    return None


def detect_language_with_fallback(
    lower: str,
    llm_client: Optional[LLMClient],
    enable_llm_fallback: bool,
) -> Optional[str]:
    detected = detect_language(lower)
    if detected:
        return detected
    if not enable_llm_fallback or llm_client is None:
        return None
    return llm_detect_language(
        llm_client=llm_client,
        enable_llm_polish=True,
        text=lower,
    )
