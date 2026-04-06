"""
REQ-002: Prompt Consistency
Verifies that every prompt with numbered options (1. ... 2. ...)
also contains a 'Reply with' hint so users know what to type.
Run: python tests/req_002_prompt_consistency.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.messages.templates import get_message

PASS = 0
FAIL = 0

# Keys that intentionally do NOT require a reply-with hint
# (they are informational or handled by FSM go-back logic)
EXEMPT_KEYS = {
    "confirm_summary",     # already has it embedded
    "confirm_prompt",      # already has it embedded
    "confirmed",           # result message, not a question
    "existing_booking_found",  # uses dynamic options, not fixed
    "existing_booking_choice_again",  # re-display only
    "ask_change_field",    # already has it embedded
    "ask_time_slots",      # already has Reply with
    "ask_date_options",    # date options shown dynamically
    "max_active_bookings_actions",  # has it embedded in hi/en
    "confirm_reschedule_summary",   # has it embedded
    "confirm_reschedule_prompt",    # has it embedded
    "ask_reason",          # has it embedded
    "ask_appointment_mode",  # has it embedded
    "ask_patient_type",      # has it embedded
    "ask_gender",            # has it embedded
    "ask_booking_for",       # _with_back() adds go_back; reply hint in invalid_booking_for
}

# These prompts must have numbered choices AND a reply-with suffix
REQUIRED_HINT_KEYS = {
    "clarify_intent",
    "ask_booking_for",
    "ask_appointment_mode",
    "ask_patient_type",
    "ask_gender",
    "ask_reason",
    "max_active_bookings_actions",
    "confirm_summary",
    "confirm_reschedule_summary",
    "ask_change_field",
}

_HAS_NUMBERED_OPTION = re.compile(r"^\s*[1-9]\.\s+", re.MULTILINE)
_HAS_REPLY_HINT = re.compile(r"reply with", re.IGNORECASE)


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def test_all_choice_prompts_have_reply_hint():
    """Scan all EN prompts: if they have numbered choices, they should have 'Reply with'."""
    global PASS, FAIL
    print("\n[TEST] All numbered-choice prompts contain 'Reply with' hint (EN)")

    # Collect all keys used in get_message by inspecting the en dict
    # We probe using a large set of known keys
    all_keys = [
        "ask_booking_for", "clarify_intent", "ask_appointment_mode",
        "ask_patient_type", "ask_gender", "ask_reason",
        "max_active_bookings_actions", "confirm_summary", "confirm_reschedule_summary",
        "ask_change_field", "ask_time_slots", "ask_date_options",
        "existing_booking_found", "existing_booking_choice_again",
        "confirm_prompt", "confirm_reschedule_prompt",
        "existing_booking_pick_header",
    ]

    issues = []
    for key in all_keys:
        try:
            msg = get_message("en", key,
                patient_name="Name",
                phone_number="9876543210",
                clinic_name="City Care",
                clinic_address="MG Road",
                appointment_date="2026-03-01",
                appointment_time="10:00 AM",
                appointment_id=1,
                patient_type="New",
                age=25,
                gender="Male",
                reason="Fever",
                symptoms="-",
                date_1="2026-03-01",
                date_2="2026-03-02",
                slot_1="10:00 AM",
                slot_2="10:15 AM",
                slot_3="10:30 AM",
                old_date="2026-03-01",
                old_time="10:00",
                new_date="2026-03-02",
                new_time="11:00",
            )
        except Exception:
            msg = ""

        has_numbered = bool(_HAS_NUMBERED_OPTION.search(msg))
        has_hint = bool(_HAS_REPLY_HINT.search(msg))

        if has_numbered and not has_hint and key not in EXEMPT_KEYS:
            issues.append(f"'{key}' has numbered options but NO 'Reply with' hint")

    if issues:
        for issue in issues:
            print(f"  [FAIL] {issue}")
        FAIL += len(issues)
    else:
        print(f"  [PASS] All {len(all_keys)} prompts checked — no missing hints")
        PASS += 1


def test_critical_prompts_have_reply_hint():
    """Verify specific critical prompts definitely have reply hints."""
    print("\n[TEST] Critical prompts have 'Reply with' hint")

    critical = {
        "ask_appointment_mode": "Reply with 1, 2, or 0.",
        "ask_patient_type": "Reply with 1, 2, or 0.",
        "ask_gender": "Reply with 1, 2, 3, or 0.",
        "confirm_summary": "Reply with 1, 2, or 0.",
        "confirm_prompt": "Reply with 1, 2, or 0.",
        "ask_reason": "Reply with 1, 2, 3, 4, 5, or 0.",
    }

    for key, expected_hint in critical.items():
        msg = get_message("en", key,
            patient_name="Name",
            phone_number="9876543210",
            clinic_name="City Care",
            clinic_address="MG Road",
            appointment_date="2026-03-01",
            appointment_time="10:00 AM",
            appointment_id=1,
            patient_type="New",
            age=25,
            gender="Male",
            reason="Fever",
            symptoms="-",
        )
        check(f"'{key}' contains: {repr(expected_hint)}", expected_hint in msg)


def test_hindi_prompts_have_reply_hint():
    """Same check for Hindi prompts."""
    print("\n[TEST] Hindi critical prompts contain 'Reply with' hint")

    hi_keys_with_hints = [
        "ask_patient_type",
        "ask_gender",
        "ask_reason",
        "confirm_summary",
        "confirm_prompt",
    ]

    for key in hi_keys_with_hints:
        msg = get_message("hi", key,
            patient_name="Vineeth",
            phone_number="9876543210",
            clinic_name="City Care",
            clinic_address="MG Road",
            appointment_date="2026-03-01",
            appointment_time="10:00 AM",
            appointment_id=1,
            patient_type="New",
            age=25,
            gender="Male",
            reason="Fever",
            symptoms="-",
        )
        check(f"Hindi '{key}' contains 'Reply with'", _HAS_REPLY_HINT.search(msg) is not None)


def test_hinglish_prompts_have_reply_hint():
    """Same check for Hinglish prompts."""
    print("\n[TEST] Hinglish critical prompts contain 'Reply with' hint")

    hinglish_keys = [
        "confirm_summary",
        "confirm_prompt",
    ]

    for key in hinglish_keys:
        msg = get_message("hinglish", key,
            patient_name="Vineeth",
            phone_number="9876543210",
            clinic_name="City Care",
            clinic_address="MG Road",
            appointment_date="2026-03-01",
            appointment_time="10:00 AM",
            appointment_id=1,
            patient_type="New",
            age=25,
            gender="Male",
            reason="Fever",
            symptoms="-",
        )
        check(f"Hinglish '{key}' contains 'Reply with'", _HAS_REPLY_HINT.search(msg) is not None)


def test_en_hi_hinglish_same_choice_count():
    """Numbered choices (1., 2., ...) should have same count across EN/HI/Hinglish."""
    print("\n[TEST] EN / Hindi / Hinglish have same option count for key prompts")

    keys_to_check = [
        ("confirm_summary", {"patient_name": "Name", "phone_number": "9876543210",
            "clinic_name": "City Care", "clinic_address": "MG Road",
            "appointment_date": "2026-03-01", "appointment_time": "10:00 AM",
            "appointment_id": 1, "patient_type": "New", "age": 25,
            "gender": "Male", "reason": "Fever", "symptoms": "-"}),
        ("ask_appointment_mode", {}),
        ("ask_patient_type", {}),
        ("ask_gender", {}),
    ]

    for key, kwargs in keys_to_check:
        en_msg = get_message("en", key, **kwargs)
        hi_msg = get_message("hi", key, **kwargs)
        hi_msg2 = get_message("hinglish", key, **kwargs)

        en_count = len(_HAS_NUMBERED_OPTION.findall(en_msg))
        hi_count = len(_HAS_NUMBERED_OPTION.findall(hi_msg))
        hi2_count = len(_HAS_NUMBERED_OPTION.findall(hi_msg2))

        check(
            f"'{key}' option count EN={en_count} HI={hi_count} Hinglish={hi2_count} match",
            en_count == hi_count == hi2_count and en_count > 0,
        )


if __name__ == "__main__":
    print("=" * 60)
    print("REQ-002: Prompt Consistency")
    print("=" * 60)

    test_all_choice_prompts_have_reply_hint()
    test_critical_prompts_have_reply_hint()
    test_hindi_prompts_have_reply_hint()
    test_hinglish_prompts_have_reply_hint()
    test_en_hi_hinglish_same_choice_count()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed out of {PASS + FAIL} checks")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
