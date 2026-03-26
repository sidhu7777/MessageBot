from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.llm.tasks import llm_extract
from src.nlu.extractors import extract_date, extract_name, extract_phone, extract_time
from src.nlu.initial_router import route_initial_decision

if TYPE_CHECKING:
    from src.fsm.appointment_fsm import AppointmentFSM


def handle_ask_booking_for_state(fsm: "AppointmentFSM", lower: str) -> str:
    choice = lower.strip()
    if choice in {"1", "a", "self", "myself"}:
        fsm.booking_for_self = True
        if fsm.known_patient_name:
            fsm.context.patient_name = fsm.known_patient_name
            fsm._ensure_actor_defaults()
            chat_phone = fsm._normalize_phone(fsm.chat_phone_number or "")
            if chat_phone and not fsm._is_telegram_channel():
                fsm.context.phone_number = chat_phone
                auto = fsm._auto_select_single_clinic_after_phone()
                if auto:
                    return fsm._respond(
                        fsm._msg("booking_for_self_ack")
                        + "\n"
                        + fsm._msg("name_ack", name=fsm.known_patient_name)
                        + "\n"
                        + fsm._msg("phone_ack", phone_number=chat_phone)
                        + "\n\n"
                        + fsm._with_back(auto)
                    )
                fsm.state = "ASK_CLINIC"
                return fsm._respond(
                    fsm._msg("booking_for_self_ack")
                    + "\n"
                    + fsm._msg("name_ack", name=fsm.known_patient_name)
                    + "\n"
                    + fsm._msg("phone_ack", phone_number=chat_phone)
                    + "\n\n"
                    + fsm._clinic_prompt()
                )
            telegram_phone = fsm.known_patient_phone
            if not telegram_phone and fsm.booking_repository:
                try:
                    raw_chat = (fsm.chat_phone_number or "").strip()
                    telegram_phone = fsm.booking_repository.find_patient_phone_by_chat_user_id(
                        chat_user_id=raw_chat,
                        admin_id=fsm.admin_id,
                        doctor_id=fsm.doctor_id,
                    )
                except Exception:
                    telegram_phone = None
            if telegram_phone:
                fsm.context.phone_number = telegram_phone
                fsm.state = "ASK_CLINIC"
                return fsm._respond(
                    fsm._msg("booking_for_self_ack")
                    + "\n"
                    + fsm._msg("name_ack", name=fsm.known_patient_name)
                    + "\n"
                    + fsm._msg("phone_ack", phone_number=telegram_phone)
                    + "\n\n"
                    + fsm._clinic_prompt()
                )
            fsm.state = "ASK_PHONE"
            return fsm._respond(
                fsm._msg("booking_for_self_ack")
                + "\n"
                + fsm._msg("name_ack", name=fsm.known_patient_name)
                + "\n\n"
                + fsm._with_back(fsm._ask_phone_prompt())
            )
        fsm.state = "ASK_NAME"
        return fsm._respond(fsm._msg("booking_for_self_ack") + "\n\n" + fsm._with_back(fsm._msg("ask_name")))
    if choice in {"2", "b", "another", "another person", "other"}:
        fsm.booking_for_self = False
        fsm.state = "ASK_NAME"
        return fsm._respond(fsm._msg("booking_for_other_ack") + "\n\n" + fsm._with_back(fsm._msg("ask_name")))
    if choice in {"0", "back", "go back"}:
        fsm.state = "INIT"
        return fsm._respond(fsm._msg("no_intent"))
    return fsm._respond(fsm._with_back(fsm._msg("invalid_booking_for"), option_count=2))


def handle_ask_name_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    name = extract_name(text)
    if not name:
        rerouted, detected_language, _ = route_initial_decision(
            llm_client=fsm.llm_client,
            enable_llm_polish=fsm.enable_llm_polish,
            text=text,
            lower=lower,
        )
        if detected_language in {"en", "hi", "hinglish"}:
            fsm.response_language = detected_language
            fsm.language_locked = True
        if rerouted in {"GENERAL_QUERY", "OTHER", "CHECK_AVAILABILITY"}:
            fsm.state = "INIT"
            fsm.init_unclear_count = 1
            return fsm._respond(fsm._main_menu_prompt(), allow_polish=False)
        return fsm._respond(fsm._msg("invalid_name"))
    normalized_name = " ".join(str(name or "").lower().split())
    normalized_self_name = " ".join(str(fsm.known_patient_name or "").lower().split())
    if not fsm.booking_for_self and normalized_name and normalized_self_name and normalized_name == normalized_self_name:
        return fsm._respond(fsm._with_back(fsm._msg("other_person_name_must_differ")))
    fsm.context.patient_name = name
    fsm._ensure_actor_defaults()
    if fsm._is_telegram_channel() and fsm.context.phone_number:
        existing_reply = fsm._existing_booking_response_for_context_identity()
        if existing_reply:
            fsm.in_edit_flow = False
            return fsm._respond(existing_reply)
    if fsm.in_edit_flow:
        fsm.in_edit_flow = False
        fsm.state = "CONFIRM"
        return fsm._respond(fsm._msg("confirm_summary", **fsm._display_context()))
    if fsm.booking_for_self:
        chat_phone = fsm._normalize_phone(fsm.chat_phone_number or "")
        if chat_phone and not fsm._is_telegram_channel():
            fsm.context.phone_number = chat_phone
            auto = fsm._auto_select_single_clinic_after_phone()
            if auto:
                return fsm._respond(
                    fsm._msg("name_ack", name=name)
                    + "\n"
                    + fsm._msg("phone_ack", phone_number=chat_phone)
                    + "\n\n"
                    + fsm._with_back(auto)
                )
            fsm.state = "ASK_CLINIC"
            return fsm._respond(
                fsm._msg("name_ack", name=name)
                + "\n"
                + fsm._msg("phone_ack", phone_number=chat_phone)
                + "\n\n"
                + fsm._clinic_prompt()
            )
    fsm.state = "ASK_PHONE"
    return fsm._respond(fsm._msg("name_ack", name=name) + "\n\n" + fsm._with_back(fsm._ask_phone_prompt()))


def handle_ask_phone_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    chat_phone = fsm._normalize_phone(fsm.chat_phone_number or "")
    normalized = lower.strip()
    same_number_markers = {
        "1",
        "yes",
        "y",
        "same",
        "same number",
        "same as this number",
        "same as whatsapp number",
        "this number",
        "chat number",
        "whatsapp number",
        "use same",
    }
    different_number_markers = {
        "2",
        "no",
        "n",
        "new",
        "different",
        "different number",
        "new number",
    }
    if normalized in same_number_markers:
        if fsm._is_telegram_channel():
            return fsm._respond(fsm._with_back(fsm._msg("invalid_phone")))
        if not chat_phone:
            return fsm._respond(fsm._with_back(fsm._msg("invalid_phone_same_missing")))
        fsm.context.phone_number = chat_phone
        if fsm._is_telegram_channel() and fsm.context.patient_name:
            existing_reply = fsm._existing_booking_response_for_context_identity()
            if existing_reply:
                fsm.in_edit_flow = False
                return fsm._respond(existing_reply)
        if fsm.in_edit_flow:
            fsm.in_edit_flow = False
            fsm.state = "CONFIRM"
            return fsm._respond(fsm._msg("confirm_summary", **fsm._display_context()))
        auto = fsm._auto_select_single_clinic_after_phone()
        if auto:
            return fsm._respond(fsm._msg("phone_ack", phone_number=fsm.context.phone_number) + "\n\n" + fsm._with_back(auto))
        fsm.state = "ASK_CLINIC"
        return fsm._respond(
            fsm._msg("phone_ack", phone_number=fsm.context.phone_number) + "\n\n" + fsm._clinic_prompt()
        )

    phone = extract_phone(text)
    if not phone and normalized in different_number_markers:
        return fsm._respond(fsm._with_back(fsm._msg("invalid_phone")))
    if not phone:
        return fsm._respond(fsm._with_back(fsm._msg("invalid_phone")))
    fsm.context.phone_number = phone
    if fsm._is_telegram_channel() and fsm.context.patient_name:
        existing_reply = fsm._existing_booking_response_for_context_identity()
        if existing_reply:
            fsm.in_edit_flow = False
            return fsm._respond(existing_reply)
    if fsm.in_edit_flow:
        fsm.in_edit_flow = False
        fsm.state = "CONFIRM"
        return fsm._respond(fsm._msg("confirm_summary", **fsm._display_context()))
    auto = fsm._auto_select_single_clinic_after_phone()
    if auto:
        return fsm._respond(fsm._msg("phone_ack", phone_number=phone) + "\n\n" + fsm._with_back(auto))
    fsm.state = "ASK_CLINIC"
    return fsm._respond(fsm._msg("phone_ack", phone_number=phone) + "\n\n" + fsm._clinic_prompt())


def handle_ask_clinic_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    if fsm.scheduling_repository and fsm.doctor_id:
        if not fsm.clinic_options_cache:
            fsm.clinic_options_cache = fsm._db_clinic_options()
        if not fsm.clinic_options_cache:
            fsm.state = "INIT"
            return fsm._respond(fsm._msg("no_clinic_available_restart"))
    selected = fsm._select_clinic(text, lower)
    if not selected:
        return fsm._respond(fsm._msg("invalid_clinic"))
    fsm.context.clinic_id = selected["id"]
    fsm.context.clinic_name = selected["name"]
    fsm.context.clinic_address = selected["address"]
    fsm.date_options_cache = []
    fsm.time_options_cache = []
    fsm.time_hour_options_cache = []
    fsm.time_slot_options_cache = []
    fsm.time_window_labels_cache = []
    fsm.selected_time_hour = None
    fsm.selected_time_period = None
    if fsm.in_edit_flow:
        fsm.context.appointment_date = None
        fsm.context.appointment_time = None
    date_options = fsm._date_options()
    if not date_options:
        fsm.state = "ASK_CLINIC"
        return fsm._respond(fsm._msg("no_date_available", clinic_name=fsm.context.clinic_name or "this clinic") + "\n\n" + fsm._clinic_prompt())
    fsm.state = "ASK_DATE"
    return fsm._respond(fsm._date_options_prompt(date_options))


def handle_ask_time_state(fsm: "AppointmentFSM", text: str, lower: str) -> str:
    date_from_text = extract_date(text)
    if date_from_text and date_from_text != fsm.context.appointment_date:
        fsm.context.appointment_date = date_from_text
        fsm.time_options_cache = []
        fsm.time_hour_options_cache = []
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
        if not fsm._load_time_options(limit=60):
            fsm.state = "ASK_DATE"
            return fsm._respond(fsm._msg("no_time_available"))
        return fsm._respond(
            fsm._msg("date_ack", appointment_date=date_from_text)
            + "\n\n"
            + fsm._initial_time_prompt()
        )

    if not fsm._load_time_options(limit=60):
        fsm.state = "ASK_DATE"
        return fsm._respond(fsm._msg("no_time_available"))

    normalized = lower.strip()

    parsed_time = extract_time(text)
    if not parsed_time:
        parsed_time = llm_extract(
            llm_client=fsm.llm_client,
            enable_llm_polish=fsm.enable_llm_polish,
            field_name="time",
            text=text,
        )

    if parsed_time and fsm._is_available_time(parsed_time):
        fsm.context.appointment_time = parsed_time
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
    else:
        if not fsm.time_slot_options_cache:
            if len(fsm.time_hour_options_cache) > 4:
                if not fsm.selected_time_period:
                    period = fsm._resolve_time_period_choice(text, normalized)
                    if not period:
                        return fsm._respond(fsm._time_period_prompt())
                    if period == "__BACK__":
                        back_text = fsm._handle_go_back()
                        if back_text:
                            return fsm._respond(back_text)
                        return fsm._respond(fsm._msg("invalid_time"))
                    fsm.selected_time_period = period
                hour_choices = fsm._hour_slot_choices_for_period(fsm.selected_time_period)
                if not hour_choices:
                    fsm.selected_time_period = None
                    return fsm._respond(fsm._time_period_prompt())
            else:
                hour_choices = fsm._hour_slot_choices_for_period(None)
                if not hour_choices:
                    return fsm._respond(fsm._msg("no_time_available"))

            fsm.time_window_labels_cache = [label for label, _ in hour_choices]
            fsm.time_slot_options_cache = [actual_time for _, actual_time in hour_choices]
            return fsm._respond(fsm._time_slot_prompt())

        selected_time: Optional[str] = None
        if normalized.isdigit():
            idx = int(normalized) - 1
            if 0 <= idx < len(fsm.time_slot_options_cache):
                selected_time = fsm.time_slot_options_cache[idx]
        parsed_time = selected_time
        if not parsed_time:
            return fsm._respond(fsm._msg("invalid_time") + "\n" + fsm._time_slot_prompt())
        if not fsm._is_available_time(parsed_time):
            return fsm._respond(
                fsm._msg("time_not_available", requested_time=parsed_time)
                + "\n"
                + fsm._time_slot_prompt()
            )
        fsm.context.appointment_time = parsed_time
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None

    if fsm.in_reschedule_flow:
        fsm.state = "CONFIRM_RESCHEDULE"
        return fsm._respond(
            fsm._msg(
                "confirm_reschedule_summary",
                old_date=fsm.existing_booking_old_date or "-",
                old_time=fsm._format_time_for_display(fsm.existing_booking_old_time or "-"),
                new_date=fsm.context.appointment_date or "-",
                new_time=fsm._format_time_for_display(fsm.context.appointment_time or "-"),
                clinic_name=fsm.context.clinic_name or "-",
            )
        )
    if fsm.in_edit_flow:
        fsm.in_edit_flow = False
        fsm.state = "CONFIRM"
        return fsm._respond(fsm._msg("confirm_summary", **fsm._display_context()))
    fsm.state = "CONFIRM"
    return fsm._respond(fsm._msg("time_ack", appointment_time=fsm._format_time_for_display(parsed_time)) + "\n\n" + fsm._msg("confirm_summary", **fsm._display_context()))
