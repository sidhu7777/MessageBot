import json
import re
from datetime import date
from typing import Any, Dict, Optional

from src.llm.client import LLMClient


def llm_extract(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    field_name: str,
    text: str,
) -> Optional[str]:
    if not enable_llm_polish:
        return None
    try:
        today_str = date.today().isoformat()
        system = (
            "Extract one field from user text for medical appointment booking. "
            "Return only value. If missing return EMPTY."
        )
        instructions = {
            "patient_type": "Output exactly one of: New, Old.",
            "age": "Output integer age only (1-120).",
            "gender": "Output exactly one of: Male, Female, Other.",
            "phone": "Output digits only (10-15 digits).",
            "date": f"Output date in YYYY-MM-DD. Today is {today_str}.",
            "time": "Output time in HH:MM 24-hour.",
        }
        user = f"Field: {field_name}. {instructions.get(field_name, '')}\nText: {text}"
        out = llm_client.generate(system, user).strip()
        if not out or out.upper() == "EMPTY":
            return None
        cleaned = out.splitlines()[0].strip()

        if field_name == "patient_type":
            return cleaned.capitalize() if cleaned.lower() in {"new", "old"} else None
        if field_name == "age":
            return cleaned if cleaned.isdigit() and 1 <= int(cleaned) <= 120 else None
        if field_name == "gender":
            normalized = cleaned.lower()
            if normalized in {"male", "m"}:
                return "Male"
            if normalized in {"female", "f"}:
                return "Female"
            if normalized in {"other", "others", "o"}:
                return "Other"
            return None
        if field_name == "phone":
            digits = re.sub(r"\D", "", cleaned)
            return digits if 10 <= len(digits) <= 15 else None
        if field_name == "date":
            return cleaned if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned) else None
        if field_name == "time":
            return cleaned if re.fullmatch(r"\d{2}:\d{2}", cleaned) else None
        return None
    except Exception:
        return None


def llm_is_booking_intent(llm_client: LLMClient, enable_llm_polish: bool, text: str) -> bool:
    if not enable_llm_polish:
        return False
    try:
        system = (
            "Classify if user wants to book a medical appointment. "
            "Return only BOOK or NONE."
        )
        out = llm_client.generate(system, f"Text: {text}").strip().upper()
        return out.startswith("BOOK")
    except Exception:
        return False


def llm_is_availability_intent(llm_client: LLMClient, enable_llm_polish: bool, text: str) -> bool:
    if not enable_llm_polish:
        return False
    try:
        system = (
            "Classify if user is asking doctor appointment availability/slots. "
            "Return only AVAIL or NONE."
        )
        out = llm_client.generate(system, f"Text: {text}").strip().upper()
        return out.startswith("AVAIL")
    except Exception:
        return False


def llm_route_intent(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    min_confidence: float = 0.70,
) -> Optional[str]:
    if not enable_llm_polish:
        return None
    try:
        system = (
            "Classify initial user intent for a medical appointment assistant. "
            "User may write in English, Hindi (Devanagari), or Hinglish. "
            "Return strict JSON only, no markdown, no extra keys: "
            "{\"intent\":\"BOOK_APPOINTMENT|CHECK_AVAILABILITY|GREETING|GENERAL_QUERY|OTHER\","
            "\"confidence\":0.0}."
        )
        raw = llm_client.generate(system, f"Text: {text}").strip()
        parsed = parse_first_json_object(raw)
        if not parsed:
            return None
        intent = str(parsed.get("intent", "")).upper()
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            return None
        allowed = {
            "BOOK_APPOINTMENT",
            "CHECK_AVAILABILITY",
            "GREETING",
            "GENERAL_QUERY",
            "OTHER",
        }
        return intent if intent in allowed else None
    except Exception:
        return None


def llm_route_intent_and_language(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    min_confidence: float = 0.70,
) -> tuple[Optional[str], Optional[str]]:
    if not enable_llm_polish:
        return None, None
    try:
        system = (
            "Classify initial user message for a medical appointment assistant. "
            "User may write in English, Hindi (Devanagari), or Hinglish. "
            "Return strict JSON only, no markdown, no extra keys: "
            "{\"intent\":\"BOOK_APPOINTMENT|CHECK_AVAILABILITY|GREETING|GENERAL_QUERY|OTHER\","
            "\"language\":\"EN|HI|HINGLISH|UNKNOWN\",\"confidence\":0.0}."
        )
        raw = llm_client.generate(system, f"Text: {text}").strip()
        parsed = parse_first_json_object(raw)
        if not parsed:
            return None, None

        intent = str(parsed.get("intent", "")).upper()
        lang = str(parsed.get("language", "")).upper()
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0

        if confidence < min_confidence:
            return None, None

        allowed_intents = {
            "BOOK_APPOINTMENT",
            "CHECK_AVAILABILITY",
            "GREETING",
            "GENERAL_QUERY",
            "OTHER",
        }
        intent_value = intent if intent in allowed_intents else None

        if lang == "EN":
            lang_value = "en"
        elif lang == "HI":
            lang_value = "hi"
        elif lang == "HINGLISH":
            lang_value = "hinglish"
        else:
            lang_value = None
        return intent_value, lang_value
    except Exception:
        return None, None


def parse_first_json_object(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = raw[start:end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def llm_detect_confirm_intent(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> str:
    if not enable_llm_polish:
        return "unknown"
    try:
        system = (
            "You are intent classifier for appointment confirmation. "
            "Return exactly one token: YES, NO, CHANGE, UNKNOWN."
        )
        out = llm_client.generate(system, f"User: {text}").strip().upper()
        if out.startswith("YES"):
            return "yes"
        if out.startswith("NO"):
            return "no"
        if out.startswith("CHANGE"):
            return "change"
        return "unknown"
    except Exception:
        return "unknown"


def llm_change_target(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> Optional[str]:
    if not enable_llm_polish:
        return None
    try:
        system = (
            "Map user requested field to one label only: "
            "ASK_NAME, ASK_PATIENT_TYPE, ASK_AGE, ASK_GENDER, ASK_PHONE, ASK_REASON, "
            "ASK_SYMPTOMS, ASK_DATE, ASK_TIME, UNKNOWN."
        )
        out = llm_client.generate(system, f"User: {text}").strip().upper()
        allowed = {
            "ASK_NAME",
            "ASK_PATIENT_TYPE",
            "ASK_AGE",
            "ASK_GENDER",
            "ASK_PHONE",
            "ASK_REASON",
            "ASK_SYMPTOMS",
            "ASK_DATE",
            "ASK_TIME",
        }
        return out if out in allowed else None
    except Exception:
        return None


def llm_detect_language(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> Optional[str]:
    if not enable_llm_polish:
        return None
    try:
        system = (
            "Detect user message language for WhatsApp medical assistant. "
            "Possible labels: EN, HI, HINGLISH, UNKNOWN. "
            "Return strict JSON only: {\"language\":\"EN|HI|HINGLISH|UNKNOWN\",\"confidence\":0.0}"
        )
        raw = llm_client.generate(system, f"Text: {text}").strip()
        parsed = parse_first_json_object(raw)
        if not parsed:
            return None
        lang = str(parsed.get("language", "")).upper()
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.60:
            return None
        if lang == "EN":
            return "en"
        if lang == "HI":
            return "hi"
        if lang == "HINGLISH":
            return "hinglish"
        return None
    except Exception:
        return None
