import re
from datetime import date, datetime, timedelta
from typing import Any, Optional


def capture_prefill_entities(context: Any, text: str) -> None:
    if not getattr(context, "appointment_date", None):
        date_value = extract_date(text)
        if date_value:
            context.appointment_date = date_value

    if not getattr(context, "appointment_time", None):
        time_value = extract_time(text)
        if time_value:
            context.appointment_time = time_value


def is_booking_intent(lower: str) -> bool:
    devanagari_patterns = [
        r"अपॉइंटमेंट",
        r"अपॉइन्टमेंट",
        r"बुकिंग",
        r"चेकअप",
        r"कंसल्ट",
        r"मुलाकात",
        r"डॉक्टर\s+से\s+मिलना",
        r"डॉक्टर\s+के\s+पास\s+जाना",
    ]
    patterns = [
        r"\bbook\b",
        r"\bbooking\b",
        r"\bappointment\b",
        r"\bapointment\b",
        r"\bconsult\b",
        r"\bcheckup\b",
        r"\bbuk\b",
        r"\bbook\s+karna\b",
        r"\bbook\s+krna\b",
        r"\bbook\s+karo\b",
        r"\bbook\s+kar\s+do\b",
        r"\bappointment\s+chahiye\b",
        r"\bschedule\b.*\bappointment\b",
    ]
    return any(re.search(pattern, lower) for pattern in patterns) or any(
        re.search(pattern, lower) for pattern in devanagari_patterns
    )


def has_weak_booking_signal(lower: str) -> bool:
    patterns = [
        r"\bdoctor\s+se\s+milna\b",
        r"\bdoctor\s+consult\b",
        r"\bmeet\s+the\s+doctor\b",
        r"\bdoctor\s+meeting\b",
    ]
    devanagari_patterns = [
        r"à¤¡à¥‰à¤•à¥à¤Ÿà¤°\s+à¤¸à¥‡\s+à¤®à¤¿à¤²à¤¨à¤¾",
        r"à¤¡à¥‰à¤•à¥à¤Ÿà¤°\s+à¤•à¥‡\s+à¤ªà¤¾à¤¸\s+à¤œà¤¾à¤¨à¤¾",
    ]
    return any(re.search(pattern, lower) for pattern in patterns) or any(
        re.search(pattern, lower) for pattern in devanagari_patterns
    )


def has_booking_negative_signal(lower: str) -> bool:
    patterns = [
        r"\bno\s+appointment\b",
        r"\bnot\s+appointment\b",
        r"\bnot\s+booking\b",
        r"\bdon[’']?t\s+book\b",
        r"\bwithout\s+appointment\b",
        r"\bno\s+book\b",
        r"\bnot\s+book\b",
        r"\bhouse\b",
        r"\bhome\b",
        r"\binterview\b",
        r"\bnahi\b",
        r"\bnah\b",
        r"à¤¨à¤¹à¥€à¤‚\s+à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ",
        r"à¤¬à¤¿à¤¨à¤¾\s+à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ",
    ]
    return any(re.search(pattern, lower) for pattern in patterns)


def is_availability_intent(lower: str) -> bool:
    devanagari_patterns = [
        r"उपलब्धता",
        r"उपलब्ध",
        r"स्लॉट",
        r"खाली",
        r"फ्री",
    ]
    patterns = [
        r"\bavailability\b",
        r"\bavailabilty\b",
        r"\bavailabilyty\b",
        r"\bavailablity\b",
        r"\bavailab\w*\b",
        r"\bavailable\b",
        r"\bslot\b",
        r"\bslots\b",
        r"\bfree\b",
        r"\bkhali\b",
        r"\bdoctor\b.*\bavailable\b",
        r"\bdoctor\b.*\bfree\b",
        r"\bdoctor\s+available\s+hai\b",
        r"\bdoctor\s+free\s+hai\b",
        r"\bfree\s+slot\b",
        r"\bavailable\s+slot\b",
        r"\bslot\s+available\b",
    ]
    return any(re.search(pattern, lower) for pattern in patterns) or any(
        re.search(pattern, lower) for pattern in devanagari_patterns
    )


def is_greeting_intent(lower: str) -> bool:
    return bool(re.search(r"\b(hi|hello|hey|hii|namaste)\b|नमस्ते|नमस्कार|हेलो", lower))


def is_restart_intent(lower: str) -> bool:
    return lower in {"restart", "reset", "start over", "new appointment", "new booking"}


def is_end_intent(lower: str) -> bool:
    if re.fullmatch(r"\s*end(\s+now)?\s*", lower):
        return True
    return any(token in lower for token in ["end process", "stop", "cancel", "quit", "exit"])


def is_yes(lower: str) -> bool:
    return bool(re.search(r"\b(yes|y|confirm|confirmed|ok|done)\b", lower))


def is_no(lower: str) -> bool:
    return bool(re.search(r"\b(no|n|nah|not now|change|edit|modify)\b", lower))


def resolve_change_target(lower: str) -> Optional[str]:
    if "appointment type" in lower or "online" in lower or "walkin" in lower or "walk-in" in lower or "clinic visit" in lower:
        return "ASK_APPOINTMENT_MODE"
    if "time" in lower:
        return "ASK_TIME"
    if "date" in lower or "day" in lower:
        return "ASK_DATE"
    if "clinic" in lower or "branch" in lower or "location" in lower:
        return "ASK_CLINIC"
    if "phone" in lower or "number" in lower or "contact" in lower:
        return "ASK_PHONE"
    if "name" in lower:
        return "ASK_NAME"
    return None


def extract_name(text: str) -> Optional[str]:
    cleaned = " ".join((text or "").strip().split())
    cleaned = cleaned.strip(".,:-")
    if cleaned.lower() in {"hi", "hello", "hey", "namaste", "hii", "end", "end now"}:
        return None
    reject_tokens = {
        "no",
        "not",
        "need",
        "doctor",
        "appointment",
        "book",
        "booking",
        "hospital",
        "house",
        "home",
        "meet",
        "cancel",
        "restart",
    }
    lowered_clean = cleaned.lower()
    if any(token in lowered_clean.split() for token in reject_tokens):
        return None

    patterns = [
        r"^\s*(?:my\s+name\s+is|i\s+am|this\s+is)\s*[:\-]?\s*([a-zA-Z][a-zA-Z ]{1,48})\s*$",
        r"^\s*(?:mera\s+naam|mai|main)\s*[:\-]?\s*([a-zA-Z][a-zA-Z ]{1,48})\s*$",
        r"^\s*(?:name)\s*[:\-]\s*([a-zA-Z][a-zA-Z ]{1,48})\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return clean_name(match.group(1))

    if re.fullmatch(r"[a-zA-Z][a-zA-Z ]{1,48}", cleaned):
        return clean_name(cleaned)
    return None


def clean_name(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    cleaned = cleaned.strip(".,:-")
    intro_patterns = [
        r"^(my\s+name\s+is)\s+",
        r"^(i\s+am)\s+",
        r"^(this\s+is)\s+",
        r"^(mera\s+naam)\s+",
        r"^(mera\s+nam)\s+",
        r"^(mera\s+na\s+nam)\s+",
        r"^(mera\s+name)\s+",
        r"^(mai)\s+",
        r"^(main)\s+",
    ]
    lowered = cleaned.lower()
    changed = True
    while changed:
        changed = False
        for pattern in intro_patterns:
            match = re.search(pattern, lowered)
            if match:
                cleaned = cleaned[match.end():].strip()
                cleaned = cleaned.lstrip(".,:- ").strip()
                lowered = cleaned.lower()
                changed = True
                break
    return " ".join(part.capitalize() for part in cleaned.split())


def extract_doctor_name(text: str) -> Optional[str]:
    match = re.search(r"(?:dr\.?|doctor)\s+([a-zA-Z][a-zA-Z ]{1,48})", text, flags=re.IGNORECASE)
    if not match:
        return None
    candidate = " ".join(part for part in match.group(1).split() if part).strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    blocked_tokens = {
        "availability",
        "availabilty",
        "availabilyty",
        "availablity",
        "available",
        "slot",
        "slots",
        "today",
        "tomorrow",
        "date",
        "time",
    }
    words = lowered.split()
    if any(token in blocked_tokens for token in words):
        return None
    return " ".join(part.capitalize() for part in candidate.split())


def extract_phone(text: str) -> Optional[str]:
    digits = re.sub(r"\D", "", text)
    if len(digits) != 10:
        return None
    return digits


def extract_date(text: str) -> Optional[str]:
    lower = text.strip().lower()
    today = date.today()

    if re.search(r"\btoday\b", lower):
        return today.isoformat()
    if re.search(r"\btomorrow\b", lower):
        return (today + timedelta(days=1)).isoformat()

    formats = [
        (r"^(\d{4})-(\d{2})-(\d{2})$", "%Y-%m-%d"),
        (r"^(\d{2})/(\d{2})/(\d{4})$", "%d/%m/%Y"),
        (r"^(\d{2})-(\d{2})-(\d{4})$", "%d-%m-%Y"),
    ]

    for regex, fmt in formats:
        matched = re.search(regex, lower)
        if matched:
            try:
                parsed = datetime.strptime(matched.group(0), fmt).date()
            except ValueError:
                return None
            if parsed < today:
                return None
            return parsed.isoformat()
    return None


def extract_time(text: str) -> Optional[str]:
    lower = text.strip().lower()
    lower = re.sub(r"^(at|around)\s+", "", lower).strip()

    am_pm = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", lower)
    if am_pm:
        hour = int(am_pm.group(1))
        minute = int(am_pm.group(2) or "00")
        period = am_pm.group(3)
        if period == "pm" and hour != 12:
            hour += 12
        if period == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    twenty_four = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lower)
    if twenty_four:
        return f"{int(twenty_four.group(1)):02d}:{int(twenty_four.group(2)):02d}"

    hour_only = re.search(r"^\s*([01]?\d|2[0-3])\s*$", lower)
    if hour_only:
        return f"{int(hour_only.group(1)):02d}:00"

    return None
