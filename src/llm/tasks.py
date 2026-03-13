import json
import re
from datetime import date
from typing import Any, Dict, Optional

from src.llm.client import LLMClient


def llm_classify_initial_message(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    min_confidence: float = 0.70,
) -> Dict[str, Any]:
    if not enable_llm_polish:
        return {}
    try:
        system = (
            "Classify the first user message for a medical appointment assistant. "
            "User may write in English, Hindi (Devanagari), or Hinglish, including typos. "
            "Focus on the user's main intent, not spelling quality. "
            "Mark abuse as ABUSE only if the text is clearly insulting, offensive, or abusive. "
            "Booking requests are not abuse. "
            "Return strict JSON only with these keys and no extras: "
            "{\"intent\":\"BOOK_APPOINTMENT|CHECK_AVAILABILITY|GREETING|GENERAL_QUERY|OTHER\","
            "\"language\":\"EN|HI|HINGLISH|UNKNOWN\","
            "\"abuse\":\"ABUSE|NONE\","
            "\"confidence\":0.0}."
        )
        user = (
            "Examples:\n"
            "Text: hello\n"
            "{\"intent\":\"GREETING\",\"language\":\"EN\",\"abuse\":\"NONE\",\"confidence\":0.98}\n"
            "Text: मुझे अपॉइंटमेंट बुक करनी है\n"
            "{\"intent\":\"BOOK_APPOINTMENT\",\"language\":\"HI\",\"abuse\":\"NONE\",\"confidence\":0.96}\n"
            "Text: मेरो को अपॉइंटम बुक करना है\n"
            "{\"intent\":\"BOOK_APPOINTMENT\",\"language\":\"HI\",\"abuse\":\"NONE\",\"confidence\":0.90}\n"
            "Text: mujhe appointment book karna hai\n"
            "{\"intent\":\"BOOK_APPOINTMENT\",\"language\":\"HINGLISH\",\"abuse\":\"NONE\",\"confidence\":0.95}\n"
            "Text: doctor available hai kya kal\n"
            "{\"intent\":\"CHECK_AVAILABILITY\",\"language\":\"HINGLISH\",\"abuse\":\"NONE\",\"confidence\":0.93}\n"
            "Text: hi madarchod\n"
            "{\"intent\":\"OTHER\",\"language\":\"HINGLISH\",\"abuse\":\"ABUSE\",\"confidence\":0.96}\n"
            f"Text: {text}"
        )
        raw = llm_client.generate(system, user).strip()
        parsed = parse_first_json_object(raw)
        if not parsed:
            return {}

        intent = str(parsed.get("intent", "")).upper()
        language = str(parsed.get("language", "")).upper()
        abuse = str(parsed.get("abuse", "")).upper()
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0

        allowed_intents = {
            "BOOK_APPOINTMENT",
            "CHECK_AVAILABILITY",
            "GREETING",
            "GENERAL_QUERY",
            "OTHER",
        }
        intent_value = intent if intent in allowed_intents else None

        if language == "EN":
            language_value = "en"
        elif language == "HI":
            language_value = "hi"
        elif language == "HINGLISH":
            language_value = "hinglish"
        else:
            language_value = None

        if not intent_value:
            return {}

        return {
            "intent": intent_value,
            "language": language_value,
            "abuse": abuse == "ABUSE",
            "confidence": confidence if confidence >= 0.0 else min_confidence,
        }
    except Exception:
        return {}


def llm_extract_booking_prefill(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> Dict[str, str]:
    if not enable_llm_polish:
        return {}
    try:
        today_str = date.today().isoformat()
        system = (
            "Extract booking prefill fields from first user message for a medical assistant. "
            "Return strict JSON only with keys: "
            "{\"patient_name\":\"\",\"appointment_date\":\"\",\"appointment_time\":\"\",\"clinic_name\":\"\",\"booking_for\":\"self|other|unknown\"}. "
            f"Today's date is {today_str}. "
            "If a field is missing, keep it as empty string."
        )
        raw = llm_client.generate(system, f"Text: {text}").strip()
        parsed = parse_first_json_object(raw)
        if not parsed:
            return {}

        patient_name = str(parsed.get("patient_name", "") or "").strip()
        appointment_date = str(parsed.get("appointment_date", "") or "").strip()
        appointment_time = str(parsed.get("appointment_time", "") or "").strip()
        clinic_name = str(parsed.get("clinic_name", "") or "").strip()
        booking_for = str(parsed.get("booking_for", "") or "").strip().lower()

        out: Dict[str, str] = {}
        if patient_name:
            out["patient_name"] = patient_name
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", appointment_date):
            out["appointment_date"] = appointment_date
        if re.fullmatch(r"\d{2}:\d{2}", appointment_time):
            out["appointment_time"] = appointment_time
        if clinic_name:
            out["clinic_name"] = clinic_name
        if booking_for in {"self", "other", "unknown"}:
            out["booking_for"] = booking_for
        return out
    except Exception:
        return {}


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
            "phone": "Output digits only (10-15 digits).",
            "date": f"Output date in YYYY-MM-DD. Today is {today_str}.",
            "time": "Output time in HH:MM 24-hour.",
        }
        user = f"Field: {field_name}. {instructions.get(field_name, '')}\nText: {text}"
        out = llm_client.generate(system, user).strip()
        if not out or out.upper() == "EMPTY":
            return None
        cleaned = out.splitlines()[0].strip()

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


def llm_route_intent_and_language(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
    min_confidence: float = 0.70,
) -> tuple[Optional[str], Optional[str]]:
    parsed = llm_classify_initial_message(
        llm_client=llm_client,
        enable_llm_polish=enable_llm_polish,
        text=text,
        min_confidence=min_confidence,
    )
    return parsed.get("intent"), parsed.get("language")


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
            "ASK_NAME, ASK_PHONE, ASK_DATE, ASK_TIME, ASK_CLINIC, ASK_APPOINTMENT_MODE, UNKNOWN."
        )
        out = llm_client.generate(system, f"User: {text}").strip().upper()
        allowed = {
            "ASK_NAME",
            "ASK_PHONE",
            "ASK_DATE",
            "ASK_TIME",
            "ASK_CLINIC",
            "ASK_APPOINTMENT_MODE",
        }
        return out if out in allowed else None
    except Exception:
        return None


def llm_detect_language(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> Optional[str]:
    parsed = llm_classify_initial_message(
        llm_client=llm_client,
        enable_llm_polish=enable_llm_polish,
        text=text,
        min_confidence=0.60,
    )
    return parsed.get("language")


def llm_detect_abuse(
    llm_client: LLMClient,
    enable_llm_polish: bool,
    text: str,
) -> bool:
    parsed = llm_classify_initial_message(
        llm_client=llm_client,
        enable_llm_polish=enable_llm_polish,
        text=text,
        min_confidence=0.70,
    )
    return bool(parsed.get("abuse"))
