from __future__ import annotations

from typing import TYPE_CHECKING

from src.nlu.extractors import is_no, is_yes

if TYPE_CHECKING:
    from src.fsm.appointment_fsm import AppointmentFSM


def handle_existing_booking_action_state(fsm: "AppointmentFSM", lower: str) -> str:
    normalized = lower.strip()
    if normalized in {"1", "keep", "no", "do not cancel"}:
        fsm.state = "COMPLETED"
        return fsm._respond(fsm._msg("existing_booking_keep"))
    if normalized in {"2", "cancel", "cancel only"}:
        choices = fsm._active_booking_rows_for_chat_phone()
        if len(choices) > 1:
            fsm.active_booking_options_cache = choices
            fsm.pending_existing_action = "cancel"
            fsm.state = "ASK_EXISTING_BOOKING_PICK"
            return fsm._respond(fsm._existing_booking_pick_prompt())
        if choices:
            fsm._set_existing_booking_from_row(choices[0])
        if fsm.booking_repository and fsm.existing_appointment_id:
            cancelled = fsm.booking_repository.cancel_appointment(
                appointment_id=fsm.existing_appointment_id,
                admin_id=fsm.admin_id,
            )
            if not cancelled:
                fsm.state = "COMPLETED"
                return fsm._respond(fsm._msg("existing_booking_cancel_failed"))
        fsm.existing_appointment_id = None
        fsm._reset_existing_booking_flags()
        fsm.state = "COMPLETED"
        return fsm._respond(fsm._msg("existing_booking_cancel_only_done"))
    if normalized in {"3", "rebook", "reschedule", "cancel and rebook", "yes"}:
        choices = fsm._active_booking_rows_for_chat_phone()
        if len(choices) > 1:
            fsm.active_booking_options_cache = choices
            fsm.pending_existing_action = "reschedule"
            fsm.state = "ASK_EXISTING_BOOKING_PICK"
            return fsm._respond(fsm._existing_booking_pick_prompt())
        if choices:
            fsm._set_existing_booking_from_row(choices[0])
        fsm.in_reschedule_flow = True
        fsm.context.clinic_id = None
        fsm.context.clinic_name = None
        fsm.context.clinic_address = None
        fsm.context.appointment_date = None
        fsm.context.appointment_time = None
        fsm.clinic_options_cache = []
        fsm.date_options_cache = []
        fsm.time_options_cache = []
        fsm.time_hour_options_cache = []
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
        fsm.state = "ASK_CLINIC"
        return fsm._respond(fsm._clinic_prompt())
    if normalized in {"4", "book for another", "another person"}:
        fsm.booking_for_self = False
        fsm.context.patient_name = None
        fsm.context.phone_number = None
        fsm.context.appointment_date = None
        fsm.context.appointment_time = None
        fsm.context.clinic_id = None
        fsm.context.clinic_name = None
        fsm.context.clinic_address = None
        fsm.clinic_options_cache = []
        fsm.date_options_cache = []
        fsm.time_options_cache = []
        fsm.time_hour_options_cache = []
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
        fsm.in_edit_flow = False
        fsm.existing_appointment_id = None
        fsm._reset_existing_booking_flags()
        fsm.state = "ASK_NAME"
        return fsm._respond(
            fsm._msg("booking_for_other_ack") + "\n\n" + fsm._with_back(fsm._msg("ask_name"))
        )
    if normalized in {"5", "show all", "list all", "all bookings"}:
        rows = fsm._active_booking_rows_for_chat_phone()
        if not rows:
            fsm._reset_existing_booking_flags()
            fsm.existing_appointment_id = None
            fsm.state = "INIT"
            return fsm._respond(fsm._welcome_greeting() + "\n" + fsm._main_menu_prompt(), allow_polish=False)
        lines = [fsm._msg("existing_booking_list_header")]
        for idx, row in enumerate(rows, start=1):
            display_number = row.get("booking_number")
            if display_number is None:
                display_number = row.get("appointment_id")
            lines.append(
                f"{idx}. Name: {row.get('patient_name') or '-'}, Clinic: {row.get('clinic_name') or '-'}, "
                f"Date: {row.get('slot_date') or '-'}, Time: {fsm._format_time_for_display(str(row.get('slot_time') or '-'))}, "
                f"Booking Number: {display_number}"
            )
        lines.append(fsm._msg("existing_booking_choice_again"))
        return fsm._respond("\n".join(lines))
    if normalized in {"0", "back", "go back"}:
        fsm.state = "INIT"
        return fsm._respond(fsm._welcome_greeting() + "\n" + fsm._main_menu_prompt(), allow_polish=False)
    return fsm._respond(fsm._msg("existing_booking_choice_again"))


def handle_max_active_bookings_action_state(fsm: "AppointmentFSM", lower: str) -> str:
    normalized = lower.strip()
    if normalized in {"1", "cancel", "cancel only"}:
        choices = fsm._active_booking_rows_for_chat_phone()
        if len(choices) > 1:
            fsm.active_booking_options_cache = choices
            fsm.pending_existing_action = "cancel"
            fsm.state = "ASK_EXISTING_BOOKING_PICK"
            return fsm._respond(fsm._existing_booking_pick_prompt())
        if choices:
            fsm._set_existing_booking_from_row(choices[0])
        if fsm.booking_repository and fsm.existing_appointment_id:
            cancelled = fsm.booking_repository.cancel_appointment(
                appointment_id=fsm.existing_appointment_id,
                admin_id=fsm.admin_id,
            )
            if not cancelled:
                fsm.state = "COMPLETED"
                return fsm._respond(fsm._msg("existing_booking_cancel_failed"))
        fsm.existing_appointment_id = None
        fsm._reset_existing_booking_flags()
        fsm.state = "COMPLETED"
        return fsm._respond(fsm._msg("existing_booking_cancel_only_done"))
    if normalized in {"2", "reschedule", "rebook", "cancel and rebook"}:
        choices = fsm._active_booking_rows_for_chat_phone()
        if len(choices) > 1:
            fsm.active_booking_options_cache = choices
            fsm.pending_existing_action = "reschedule"
            fsm.state = "ASK_EXISTING_BOOKING_PICK"
            return fsm._respond(fsm._existing_booking_pick_prompt())
        if choices:
            fsm._set_existing_booking_from_row(choices[0])
        fsm.in_reschedule_flow = True
        fsm.context.clinic_id = None
        fsm.context.clinic_name = None
        fsm.context.clinic_address = None
        fsm.context.appointment_date = None
        fsm.context.appointment_time = None
        fsm.clinic_options_cache = []
        fsm.date_options_cache = []
        fsm.time_options_cache = []
        fsm.time_hour_options_cache = []
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
        fsm.state = "ASK_CLINIC"
        return fsm._respond(fsm._clinic_prompt())
    if normalized in {"0", "back", "go back"}:
        fsm.state = "ASK_EXISTING_BOOKING_ACTION"
        return fsm._respond(fsm._msg("existing_booking_choice_again"))
    return fsm._respond(fsm._msg("max_active_bookings_invalid"))


def handle_existing_booking_pick_state(fsm: "AppointmentFSM", lower: str) -> str:
    normalized = lower.strip()
    if normalized in {"0", "back", "go back"}:
        fsm.state = "ASK_EXISTING_BOOKING_ACTION"
        fsm.pending_existing_action = None
        return fsm._respond(fsm._msg("existing_booking_choice_again"))
    if not normalized.isdigit():
        return fsm._respond(fsm._existing_booking_pick_prompt())
    idx = int(normalized) - 1
    if idx < 0 or idx >= len(fsm.active_booking_options_cache):
        return fsm._respond(fsm._existing_booking_pick_prompt())
    selected = fsm.active_booking_options_cache[idx]
    fsm._set_existing_booking_from_row(selected)
    action = (fsm.pending_existing_action or "").strip().lower()
    fsm.pending_existing_action = None
    fsm.active_booking_options_cache = []
    if action == "cancel":
        if fsm.booking_repository and fsm.existing_appointment_id:
            cancelled = fsm.booking_repository.cancel_appointment(
                appointment_id=fsm.existing_appointment_id,
                admin_id=fsm.admin_id,
            )
            if not cancelled:
                fsm.state = "COMPLETED"
                return fsm._respond(fsm._msg("existing_booking_cancel_failed"))
        fsm.existing_appointment_id = None
        fsm._reset_existing_booking_flags()
        fsm.state = "COMPLETED"
        return fsm._respond(fsm._msg("existing_booking_cancel_only_done"))
    if action == "reschedule":
        fsm.in_reschedule_flow = True
        fsm.context.clinic_id = None
        fsm.context.clinic_name = None
        fsm.context.clinic_address = None
        fsm.context.appointment_date = None
        fsm.context.appointment_time = None
        fsm.clinic_options_cache = []
        fsm.date_options_cache = []
        fsm.time_options_cache = []
        fsm.time_hour_options_cache = []
        fsm.time_slot_options_cache = []
        fsm.time_window_labels_cache = []
        fsm.selected_time_hour = None
        fsm.selected_time_period = None
        fsm.state = "ASK_CLINIC"
        return fsm._respond(fsm._clinic_prompt())
    fsm.state = "ASK_EXISTING_BOOKING_ACTION"
    return fsm._respond(fsm._msg("existing_booking_choice_again"))


def handle_confirm_reschedule_state(fsm: "AppointmentFSM", lower: str) -> str:
    normalized = lower.strip()
    if normalized == "1" or is_yes(lower):
        if not fsm.booking_repository or not fsm.existing_appointment_id:
            fsm.in_reschedule_flow = False
            fsm.state = "COMPLETED"
            return fsm._respond(fsm._msg("existing_booking_cancel_failed"))
        resolved_time = fsm._resolve_current_reschedule_time()
        if not resolved_time:
            fsm.in_reschedule_flow = False
            fsm.state = "COMPLETED"
            return fsm._respond(fsm._msg("reschedule_failed"))
        fsm.context.appointment_time = resolved_time
        result = fsm.booking_repository.reschedule_appointment_same_clinic(
            appointment_id=fsm.existing_appointment_id,
            new_date=fsm.context.appointment_date or "",
            new_time=resolved_time,
            new_clinic_id=int(fsm.context.clinic_id) if fsm.context.clinic_id else None,
            admin_id=fsm.admin_id,
        )
        fsm.in_reschedule_flow = False
        fsm.state = "COMPLETED"
        if result.ok:
            display_number = result.queue_number if result.queue_number is not None else result.appointment_id
            return fsm._respond(
                fsm._msg(
                    "reschedule_confirmed",
                    appointment_id=display_number,
                    clinic_name=fsm.context.clinic_name or "-",
                    appointment_date=fsm.context.appointment_date or "-",
                    appointment_time=fsm._format_time_for_display(fsm.context.appointment_time or "-"),
                )
            )
        return fsm._respond(fsm._msg("reschedule_failed"))
    if normalized == "2":
        if fsm._is_telegram_channel():
            return fsm._respond(fsm._msg("confirm_reschedule_prompt"))
        fsm.state = "ASK_TIME"
        return fsm._respond(fsm._time_prompt_for_edit_flow())
    if normalized == "0" or is_no(lower):
        fsm.in_reschedule_flow = False
        fsm.state = "ASK_EXISTING_BOOKING_ACTION"
        return fsm._respond(fsm._msg("existing_booking_choice_again"))
    return fsm._respond(fsm._msg("confirm_reschedule_prompt"))
