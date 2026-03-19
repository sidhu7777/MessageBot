from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, TYPE_CHECKING

from src.llm.tasks import llm_change_target, llm_extract
from src.nlu.extractors import (
    extract_date,
    extract_doctor_name,
    is_greeting_intent,
    is_booking_intent,
    is_no,
    is_restart_intent,
    is_yes,
    resolve_change_target,
)
from src.nlu.initial_router import route_initial_decision

if TYPE_CHECKING:
    from src.fsm.appointment_fsm import AppointmentFSM


def handle_init_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    def _clarify_options_only() -> str:
        return fsm._msg("clarify_intent")

    def _booking_start_reply() -> str:
        return (
            fsm._welcome_booking_start()
            + "\n"
            + fsm._msg("intent_ack")
            + "\n\n"
            + fsm._with_back(fsm._msg("ask_booking_for"), option_count=2)
        )

    def _booking_continue_reply() -> str:
        return (
            fsm._msg("intent_ack")
            + "\n\n"
            + fsm._with_back(fsm._msg("ask_booking_for"), option_count=2)
        )

    def _language_choice(value: str) -> str | None:
        normalized = (value or "").strip().lower()
        if normalized in {"1", "english", "en"}:
            return "en"
        if normalized in {"2", "hindi", "hi", "हिंदी", "हिन्दी"}:
            return "hi"
        if normalized in {"3", "hinglish"}:
            return "hinglish"
        return None

    def _language_already_selected() -> bool:
        return bool(fsm.language_selected_by_user and fsm.response_language in {"en", "hi", "hinglish"})

    def _continue_after_language_selection() -> str:
        routed = fsm.pending_init_intent or "OTHER"
        fsm.pending_init_intent = None
        fsm.init_unclear_count = 0
        if routed == "BOOK_APPOINTMENT":
            existing_reply = fsm._existing_booking_entry_response()
            if existing_reply:
                return existing_reply
            fsm.state = "ASK_BOOKING_FOR"
            return _booking_continue_reply()
        if routed == "CHECK_AVAILABILITY":
            fsm.state = "ASK_AVAILABILITY_DATE"
            fsm.availability_date_options_cache = []
            date_opts = fsm._availability_date_options()
            fsm.availability_date_options_cache = date_opts
            return fsm._availability_date_options_prompt(date_opts)
        if routed == "GREETING":
            fsm.state = "INIT"
            return _clarify_options_only()
        fsm.state = "INIT"
        return _clarify_options_only()

    if fsm.state == "ASK_LANGUAGE":
        chosen_language = _language_choice(lower)
        if not chosen_language:
            return fsm._respond(fsm._msg("invalid_language_selection"), allow_polish=False)
        fsm.response_language = chosen_language
        fsm.language_locked = True
        fsm.language_selected_by_user = True
        return fsm._respond(_continue_after_language_selection(), allow_polish=False)

    if fsm._is_telegram_start_command(lower):
        fsm.init_unclear_count = 0
        if _language_already_selected():
            return fsm._respond(_clarify_options_only(), allow_polish=False)
        fsm.pending_init_intent = "GREETING"
        fsm.state = "ASK_LANGUAGE"
        return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)

    if lower.strip() == "1":
        if _language_already_selected():
            fsm.init_unclear_count = 0
            existing_reply = fsm._existing_booking_entry_response()
            if existing_reply:
                return fsm._respond(existing_reply, allow_polish=False)
            fsm.state = "ASK_BOOKING_FOR"
            return fsm._respond(_booking_continue_reply(), allow_polish=False)
        fsm.pending_init_intent = "BOOK_APPOINTMENT"
        fsm.state = "ASK_LANGUAGE"
        return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)
    if lower.strip() == "2":
        if _language_already_selected():
            fsm.init_unclear_count = 0
            fsm.state = "ASK_AVAILABILITY_DATE"
            fsm.availability_date_options_cache = []
            date_opts = fsm._availability_date_options()
            fsm.availability_date_options_cache = date_opts
            return fsm._respond(fsm._availability_date_options_prompt(date_opts), allow_polish=False)
        fsm.pending_init_intent = "CHECK_AVAILABILITY"
        fsm.state = "ASK_LANGUAGE"
        return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)
    if lower.strip() == "0" and _language_already_selected():
        fsm.pending_init_intent = "GREETING"
        fsm.state = "ASK_LANGUAGE"
        return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)

    if fsm.init_unclear_count >= 3:
        if is_yes(lower) or is_booking_intent(lower):
            fsm.init_unclear_count = 0
            existing_reply = fsm._existing_booking_entry_response()
            if existing_reply:
                return fsm._respond(existing_reply, allow_polish=False)
            fsm.state = "ASK_BOOKING_FOR"
            return fsm._respond(_booking_start_reply(), allow_polish=False)
        fsm._reset_all(cancelled=True)
        return fsm._respond(fsm._msg("non_scope_final"), allow_polish=False)

    routed, detected_language, abuse_detected = route_initial_decision(
        llm_client=fsm.llm_client,
        enable_llm_polish=fsm.enable_llm_polish,
        text=text,
        lower=lower,
    )
    if detected_language in {"en", "hi", "hinglish"}:
        fsm.response_language = detected_language
        fsm.language_locked = True
    if abuse_detected or routed == "ABUSE":
        fsm.context.abusive_warning_count = int(fsm.context.abusive_warning_count or 0) + 1
        if fsm.context.abusive_warning_count >= 2:
            fsm.context.abuse_blocked = True
            return fsm._respond(fsm._msg("abusive_language_final"), allow_polish=False)
        return fsm._respond(fsm._msg("abusive_language"), allow_polish=False)
    if _language_already_selected():
        if routed == "BOOK_APPOINTMENT":
            existing_reply = fsm._existing_booking_entry_response()
            if existing_reply:
                return fsm._respond(existing_reply, allow_polish=False)
            fsm.state = "ASK_BOOKING_FOR"
            return fsm._respond(_booking_start_reply(), allow_polish=False)
        if routed == "CHECK_AVAILABILITY":
            fsm.init_unclear_count = 0
            fsm.state = "ASK_AVAILABILITY_DATE"
            fsm.availability_date_options_cache = []
            date_opts = fsm._availability_date_options()
            fsm.availability_date_options_cache = date_opts
            return fsm._respond(fsm._availability_date_options_prompt(date_opts), allow_polish=False)
        if routed == "GREETING":
            fsm.init_unclear_count = 0
            existing_reply = fsm._existing_booking_entry_response()
            if existing_reply:
                return fsm._respond(existing_reply, allow_polish=False)
            return fsm._respond(_clarify_options_only(), allow_polish=False)
        fsm.init_unclear_count += 1
        if fsm.init_unclear_count >= 3:
            return fsm._respond(fsm._msg("final_booking_check"), allow_polish=False)
        return fsm._respond(_clarify_options_only(), allow_polish=False)

    fsm.pending_init_intent = routed if routed in {"BOOK_APPOINTMENT", "CHECK_AVAILABILITY", "GREETING"} else "OTHER"
    fsm.state = "ASK_LANGUAGE"
    return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)


def handle_cancelled_state(fsm: "AppointmentFSM", lower: str) -> str:
    fsm._reset_all(cancelled=False)
    normalized = (lower or "").strip()
    if normalized == "1" or is_booking_intent(normalized) or is_restart_intent(normalized):
        fsm.pending_init_intent = "BOOK_APPOINTMENT"
    elif normalized == "2":
        fsm.pending_init_intent = "CHECK_AVAILABILITY"
    elif is_greeting_intent(normalized):
        fsm.pending_init_intent = "GREETING"
    else:
        fsm.pending_init_intent = "OTHER"
    fsm.state = "ASK_LANGUAGE"
    return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)


def handle_availability_date_state(fsm: "AppointmentFSM", lower: str) -> str:
    date_opts = fsm.availability_date_options_cache or fsm._availability_date_options()
    fsm.availability_date_options_cache = date_opts
    if lower.strip() == "0":
        back_text = fsm._handle_go_back()
        if back_text:
            return fsm._respond(back_text)
        return fsm._respond(fsm._availability_date_options_prompt(date_opts))
    try:
        choice = int(lower.strip())
    except ValueError:
        choice = None
    if choice is not None and 1 <= choice <= len(date_opts):
        selected_date = date_opts[choice - 1]
        fsm.context.availability_date = selected_date
        fsm.state = "ASK_AVAILABILITY_DETAILS"
        return fsm._respond(fsm._availability_reply(selected_date))
    return fsm._respond(fsm._availability_date_options_prompt(date_opts))


def handle_availability_details_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    if is_booking_intent(lower) or is_restart_intent(lower) or lower.strip() == "1":
        fsm.context = fsm.context.__class__()
        existing_reply = fsm._existing_booking_entry_response()
        if existing_reply:
            return fsm._respond(existing_reply)
        fsm.state = "ASK_BOOKING_FOR"
        return fsm._respond(fsm._msg("intent_ack") + "\n\n" + fsm._with_back(fsm._msg("ask_booking_for"), option_count=2))

    # Allow numeric date selection even in details state (e.g., user replies "1" or "2").
    normalized = (lower or "").strip()
    if normalized.isdigit() and fsm.availability_date_options_cache:
        idx = int(normalized)
        if 1 <= idx <= len(fsm.availability_date_options_cache):
            selected_date = fsm.availability_date_options_cache[idx - 1]
            fsm.context.availability_date = selected_date
            return fsm._respond(fsm._availability_reply(selected_date))

    doctor_name = extract_doctor_name(text)
    if doctor_name:
        fsm.context.availability_doctor = doctor_name

    availability_date = extract_date(text)
    if not availability_date:
        availability_date = llm_extract(
            llm_client=fsm.llm_client,
            enable_llm_polish=fsm.enable_llm_polish,
            field_name="date",
            text=text,
        )
    if availability_date:
        fsm.context.availability_date = availability_date

    if fsm.context.availability_date:
        return fsm._respond(fsm._availability_reply(fsm.context.availability_date))
    if not fsm.context.availability_doctor and not fsm.context.availability_date:
        return fsm._respond(fsm._msg("availability_ask"))
    return fsm._respond(fsm._msg("availability_ask_date"))


def handle_confirm_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    confirm_intent = fsm._detect_confirm_intent(text)
    if confirm_intent == "yes":
        fsm.state = "COMPLETED"
        confirmed = fsm._msg("confirmed", **fsm._display_context())
        persist_note = fsm._persist_confirmed_appointment()
        if persist_note:
            confirmed = persist_note + "\n\n" + confirmed
        return fsm._respond(confirmed)
    if confirm_intent == "back":
        back_text = fsm._handle_go_back()
        if back_text:
            return fsm._respond(back_text)
    if confirm_intent in {"no", "change"}:
        reroute_state = resolve_change_target(lower)
        if not reroute_state and confirm_intent == "change":
            reroute_state = llm_change_target(
                llm_client=fsm.llm_client,
                enable_llm_polish=fsm.enable_llm_polish,
                text=text,
            )
        if reroute_state:
            fsm.state = reroute_state
            if reroute_state == "ASK_CLINIC":
                return fsm._respond(fsm._msg("change_ack") + "\n\n" + fsm._clinic_prompt())
            if reroute_state == "ASK_TIME":
                return fsm._respond(fsm._msg("change_ack") + "\n\n" + fsm._time_prompt_for_edit_flow())
            return fsm._respond(fsm._msg("change_ack", step=reroute_state))
        fsm.state = "ASK_CHANGE_FIELD"
        return fsm._respond(fsm._with_back(fsm._msg("ask_change_field"), option_count=5))
    return fsm._respond(fsm._msg("confirm_prompt"))


def handle_change_field_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    if lower.strip() == "0":
        back_text = fsm._handle_go_back()
        if back_text:
            return fsm._respond(back_text)
    reroute_state = resolve_change_target(lower)
    if not reroute_state:
        reroute_state = fsm._change_state_from_option(lower)
    if not reroute_state:
        reroute_state = llm_change_target(
            llm_client=fsm.llm_client,
            enable_llm_polish=fsm.enable_llm_polish,
            text=text,
        )
    if not reroute_state:
        return fsm._respond(
            fsm._msg("invalid_change_field")
            + "\n"
            + fsm._with_back(fsm._msg("ask_change_field"), option_count=5)
        )
    fsm.in_edit_flow = True
    fsm.state = reroute_state
    if reroute_state == "ASK_CLINIC":
        return fsm._respond(fsm._msg("change_ack") + "\n\n" + fsm._clinic_prompt())
    if reroute_state == "ASK_TIME":
        return fsm._respond(fsm._msg("change_ack") + "\n\n" + fsm._time_prompt_for_edit_flow())
    return fsm._respond(fsm._msg("change_ack", step=reroute_state))


def handle_completed_state(fsm: "AppointmentFSM", lower: str) -> str:
    fsm._reset_all(cancelled=False)
    normalized = (lower or "").strip()
    if normalized == "1" or is_booking_intent(normalized) or is_restart_intent(normalized):
        fsm.pending_init_intent = "BOOK_APPOINTMENT"
    elif normalized == "2":
        fsm.pending_init_intent = "CHECK_AVAILABILITY"
    elif is_greeting_intent(normalized):
        fsm.pending_init_intent = "GREETING"
    else:
        fsm.pending_init_intent = "OTHER"
    fsm.state = "ASK_LANGUAGE"
    return fsm._respond(fsm._language_selection_prompt(), allow_polish=False)


def handle_ask_date_state(fsm: "AppointmentFSM", lower: str) -> str:
    normalized = lower.strip()
    date_options = fsm._date_options()
    if not date_options:
        fsm.state = "ASK_CLINIC"
        return fsm._respond(fsm._msg("no_date_available", clinic_name=fsm.context.clinic_name or "this clinic") + "\n\n" + fsm._clinic_prompt())
    if normalized.isdigit():
        index = int(normalized)
        if 1 <= index <= len(date_options):
            parsed_date = date_options[index - 1]
        else:
            return fsm._respond(
                fsm._msg("invalid_date")
                + "\n"
                + fsm._date_options_prompt(date_options)
            )
    elif normalized in {"today", "tomorrow"}:
        target = None
        today_iso = date.today().isoformat()
        tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()
        if normalized == "today":
            target = today_iso
        elif normalized == "tomorrow":
            target = tomorrow_iso
        if target and target in date_options:
            parsed_date = target
        else:
            return fsm._respond(
                fsm._msg("invalid_date")
                + "\n"
                + fsm._date_options_prompt(date_options)
            )
    else:
        return fsm._respond(
            fsm._msg("invalid_date")
            + "\n"
            + fsm._date_options_prompt(date_options)
        )
    fsm.context.appointment_date = parsed_date
    fsm.time_options_cache = []
    fsm.time_hour_options_cache = []
    fsm.time_slot_options_cache = []
    fsm.time_window_labels_cache = []
    fsm.selected_time_hour = None
    fsm.selected_time_period = None
    fsm.state = "ASK_TIME"
    if not fsm._load_time_options(limit=60):
        fsm.state = "ASK_DATE"
        return fsm._respond(fsm._msg("no_time_available"))
    return fsm._respond(
        fsm._msg("date_ack", appointment_date=parsed_date)
        + "\n\n"
        + fsm._initial_time_prompt()
    )
