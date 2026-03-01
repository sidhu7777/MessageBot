import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from src.db_store import BookingRepository, SchedulingRepository
from src.llm.client import LLMClient
from src.llm.tasks import (
    llm_change_target,
    llm_detect_abuse,
    llm_detect_confirm_intent,
    llm_extract,
    llm_extract_booking_prefill,
)
from src.messages.templates import get_message
from src.nlu.extractors import (
    extract_appointment_mode,
    capture_prefill_entities,
    extract_date,
    extract_doctor_name,
    extract_name,
    extract_phone,
    extract_time,
    is_booking_intent,
    is_end_intent,
    is_no,
    is_restart_intent,
    is_yes,
    resolve_change_target,
)
from src.nlu.initial_router import route_initial_decision
from src.nlu.language_detector import update_response_language


LOGGER = logging.getLogger(__name__)

ABUSE_TERMS_EN = {
    "fuck",
    "shit",
    "bitch",
    "asshole",
    "bastard",
    "idiot",
}

ABUSE_TERMS_HINGLISH = {
    "madarchod",
    "bhenchod",
    "chutiya",
    "harami",
    "gandu",
    "bakchod",
}

ABUSE_TERMS_HI = {
    "मादरचोद",
    "बहनचोद",
    "चूतिया",
    "हरामी",
    "गांडू",
    "भोसड़ी",
}

@dataclass
class AppointmentContext:
    patient_name: Optional[str] = None
    appointment_mode: Optional[str] = None
    patient_type: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    phone_number: Optional[str] = None
    reason: Optional[str] = None
    symptoms: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    clinic_id: Optional[str] = None
    clinic_name: Optional[str] = None
    clinic_address: Optional[str] = None
    availability_doctor: Optional[str] = None
    availability_date: Optional[str] = None
    abusive_warning_count: int = 0
    abuse_blocked: bool = False


@dataclass
class AppointmentFSM:
    llm_client: LLMClient
    mixed_response_language: str = "auto"
    enable_llm_polish: bool = True
    booking_repository: Optional[BookingRepository] = None
    scheduling_repository: Optional[SchedulingRepository] = None
    state: str = "INIT"
    context: AppointmentContext = field(default_factory=AppointmentContext)
    response_language: str = "en"
    language_locked: bool = False
    language_turn_count: int = 0
    init_unclear_count: int = 0
    chat_phone_number: Optional[str] = None
    bot_whatsapp_number: Optional[str] = None
    in_edit_flow: bool = False
    doctor_id: Optional[int] = None
    admin_id: Optional[int] = None
    clinic_options_cache: list[dict] = field(default_factory=list)
    date_options_cache: list[str] = field(default_factory=list)
    time_options_cache: list[str] = field(default_factory=list)
    time_hour_options_cache: list[str] = field(default_factory=list)
    time_slot_options_cache: list[str] = field(default_factory=list)
    time_window_labels_cache: list[str] = field(default_factory=list)
    selected_time_hour: Optional[str] = None
    selected_time_period: Optional[str] = None
    booking_for_self: Optional[bool] = None
    existing_appointment_id: Optional[int] = None
    existing_booking_clinic_id: Optional[str] = None
    existing_booking_clinic_name: Optional[str] = None
    existing_booking_doctor_id: Optional[int] = None
    existing_booking_old_date: Optional[str] = None
    existing_booking_old_time: Optional[str] = None
    in_reschedule_flow: bool = False
    active_booking_options_cache: list[dict] = field(default_factory=list)
    pending_existing_action: Optional[str] = None
    known_patient_name: Optional[str] = None

    def handle(self, user_text: str) -> str:
        text = (user_text or "").strip()
        lower = text.lower()
        text, lower = self._normalize_option_input_for_state(text, lower)

        if self.context.abuse_blocked:
            return ""

        if not text:
            return self._respond(self._msg("empty_input"))

        if self._is_abusive_message(text, lower, allow_llm=(self.state == "INIT")):
            self.context.abusive_warning_count = int(self.context.abusive_warning_count or 0) + 1
            if self.context.abusive_warning_count >= 2:
                self.context.abuse_blocked = True
                return self._respond(self._msg("abusive_language_final"), allow_polish=False)
            return self._respond(self._msg("abusive_language"), allow_polish=False)
        self.context.abusive_warning_count = 0

        if is_end_intent(lower):
            self._reset_all(cancelled=True)
            return self._respond(self._msg("ended"))

        if self._is_go_back(lower):
            go_back_reply = self._handle_go_back()
            if go_back_reply:
                return self._respond(go_back_reply)

        self._update_response_language(lower)
        capture_prefill_entities(self.context, text)

        # Ensure actor IDs and known-patient name are hydrated every turn so that
        # multi-turn sessions (where FSM is reconstructed from snapshot) always have
        # the correct known_patient_name before any state handler checks it.
        self._ensure_actor_defaults()
        self._hydrate_known_patient_name()

        if is_restart_intent(lower):
            self._reset_all(cancelled=False)
            self.state = "ASK_NAME"
            return self._respond(self._msg("restart") + "\n" + self._msg("ask_name"))

        if self.state == "INIT":
            if self._is_telegram_start_command(lower):
                self.init_unclear_count = 0
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply, allow_polish=False)
                return self._respond(self._welcome_greeting() + "\n" + self._msg("clarify_intent"), allow_polish=False)

            # Direct menu selection — patient pressed 1 or 2 from the options we showed.
            if lower.strip() == "1":
                self.init_unclear_count = 0
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply, allow_polish=False)
                self.state = "ASK_BOOKING_FOR"
                return self._respond(
                    self._msg("intent_ack") + "\n" + self._with_back(self._msg("ask_booking_for"), option_count=2),
                    allow_polish=False,
                )
            if lower.strip() == "2":
                self.init_unclear_count = 0
                self.state = "ASK_AVAILABILITY_DETAILS"
                return self._respond(self._msg("availability_intro"), allow_polish=False)

            if self.init_unclear_count >= 3:
                if is_yes(lower) or is_booking_intent(lower):
                    self.init_unclear_count = 0
                    existing_reply = self._existing_booking_entry_response()
                    if existing_reply:
                        return self._respond(existing_reply, allow_polish=False)
                    self.state = "ASK_BOOKING_FOR"
                    return self._respond(
                        self._msg("intent_ack") + "\n" + self._with_back(self._msg("ask_booking_for"), option_count=2),
                        allow_polish=False,
                    )
                self._reset_all(cancelled=True)
                return self._respond(self._msg("non_scope_final"), allow_polish=False)

            routed, detected_language = route_initial_decision(
                llm_client=self.llm_client,
                enable_llm_polish=self.enable_llm_polish,
                text=text,
                lower=lower,
            )
            if detected_language in {"en", "hi", "hinglish"}:
                self.response_language = detected_language
                self.language_locked = True
            if routed == "BOOK_APPOINTMENT":
                self.init_unclear_count = 0
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply, allow_polish=False)
                self.state = "ASK_BOOKING_FOR"
                return self._respond(
                    self._msg("intent_ack") + "\n" + self._with_back(self._msg("ask_booking_for"), option_count=2),
                    allow_polish=False,
                )
            if routed == "CHECK_AVAILABILITY":
                self.init_unclear_count = 0
                self.state = "ASK_AVAILABILITY_DETAILS"
                doctor_name = extract_doctor_name(text)
                if doctor_name:
                    self.context.availability_doctor = doctor_name
                availability_date = extract_date(text)
                if not availability_date:
                    availability_date = llm_extract(
                        llm_client=self.llm_client,
                        enable_llm_polish=self.enable_llm_polish,
                        field_name="date",
                        text=text,
                    )
                if availability_date:
                    self.context.availability_date = availability_date
                    return self._respond(self._availability_reply(availability_date), allow_polish=False)
                return self._respond(self._msg("availability_intro"), allow_polish=False)
            if routed == "GREETING":
                self.init_unclear_count = 0
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply, allow_polish=False)
                return self._respond(self._welcome_greeting() + "\n" + self._msg("clarify_intent"), allow_polish=False)
            # GENERAL_QUERY, unknown intent — always show the menu so patient can pick.
            self.init_unclear_count += 1
            if self.init_unclear_count >= 3:
                return self._respond(self._msg("final_booking_check"), allow_polish=False)
            return self._respond(self._msg("clarify_intent"), allow_polish=False)

        if self.state == "CANCELLED":
            if is_booking_intent(lower) or is_restart_intent(lower):
                self._reset_all(cancelled=False)
                self.state = "ASK_NAME"
                return self._respond(self._msg("ask_name"))
            return self._respond(self._msg("cancelled_hint"))

        if self.state == "ASK_BOOKING_FOR":
            choice = lower.strip()
            if choice in {"1", "a", "self", "myself"}:
                self.booking_for_self = True
                if self.known_patient_name:
                    self.context.patient_name = self.known_patient_name
                    self._ensure_actor_defaults()
                    chat_phone = self._normalize_phone(self.chat_phone_number or "")
                    if chat_phone and not self._is_telegram_channel():
                        self.context.phone_number = chat_phone
                        auto = self._auto_select_single_clinic_after_phone()
                        if auto:
                            return self._respond(
                                self._msg("booking_for_self_ack")
                                + "\n"
                                + self._msg("name_ack", name=self.known_patient_name)
                                + "\n"
                                + self._msg("phone_ack", phone_number=chat_phone)
                                + "\n"
                                + self._with_back(auto)
                            )
                        self.state = "ASK_CLINIC"
                        return self._respond(
                            self._msg("booking_for_self_ack")
                            + "\n"
                            + self._msg("name_ack", name=self.known_patient_name)
                            + "\n"
                            + self._msg("phone_ack", phone_number=chat_phone)
                            + "\n"
                            + self._clinic_prompt()
                        )
                    self.state = "ASK_CLINIC"
                    telegram_phone = None
                    if self.booking_repository:
                        try:
                            raw_chat = (self.chat_phone_number or "").strip()
                            telegram_phone = self.booking_repository.find_patient_phone_by_chat_user_id(
                                chat_user_id=raw_chat,
                                admin_id=self.admin_id,
                                doctor_id=self.doctor_id,
                            )
                        except Exception:
                            telegram_phone = None
                    if telegram_phone:
                        self.context.phone_number = telegram_phone
                    return self._respond(
                        self._msg("booking_for_self_ack")
                        + "\n"
                        + self._msg("name_ack", name=self.known_patient_name)
                        + "\n"
                        + self._clinic_prompt()
                    )
                self.state = "ASK_NAME"
                return self._respond(self._msg("booking_for_self_ack") + "\n" + self._with_back(self._msg("ask_name")))
            if choice in {"2", "b", "another", "another person", "other"}:
                self.booking_for_self = False
                self.state = "ASK_NAME"
                return self._respond(self._msg("booking_for_other_ack") + "\n" + self._with_back(self._msg("ask_name")))
            if choice in {"0", "back", "go back"}:
                self.state = "INIT"
                return self._respond(self._msg("no_intent"))
            return self._respond(self._with_back(self._msg("invalid_booking_for"), option_count=2))

        if self.state == "ASK_NAME":
            name = extract_name(text)
            if not name:
                rerouted, detected_language = route_initial_decision(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    text=text,
                    lower=lower,
                )
                if detected_language in {"en", "hi", "hinglish"}:
                    self.response_language = detected_language
                    self.language_locked = True
                if rerouted in {"GENERAL_QUERY", "OTHER", "CHECK_AVAILABILITY"}:
                    self.state = "INIT"
                    self.init_unclear_count = 1
                    return self._respond(self._msg("clarify_intent"), allow_polish=False)
                return self._respond(self._msg("invalid_name"))
            self.context.patient_name = name
            self._ensure_actor_defaults()
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_NAME") + "\n" + self._msg("confirm_summary", **self._display_context()))
            if self.booking_for_self:
                chat_phone = self._normalize_phone(self.chat_phone_number or "")
                if chat_phone and not self._is_telegram_channel():
                    self.context.phone_number = chat_phone
                    auto = self._auto_select_single_clinic_after_phone()
                    if auto:
                        return self._respond(
                            self._msg("name_ack", name=name)
                            + "\n"
                            + self._msg("phone_ack", phone_number=chat_phone)
                            + "\n"
                            + self._with_back(auto)
                        )
                    self.state = "ASK_CLINIC"
                    return self._respond(
                        self._msg("name_ack", name=name)
                        + "\n"
                        + self._msg("phone_ack", phone_number=chat_phone)
                        + "\n"
                        + self._clinic_prompt()
                    )
            self.state = "ASK_PHONE"
            return self._respond(self._msg("name_ack", name=name) + "\n" + self._with_back(self._ask_phone_prompt()))

        if self.state == "ASK_EXISTING_BOOKING_ACTION":
            normalized = lower.strip()
            if normalized in {"1", "keep", "no", "do not cancel"}:
                self.state = "COMPLETED"
                return self._respond(self._msg("existing_booking_keep"))
            if normalized in {"2", "cancel", "cancel only"}:
                choices = self._active_booking_rows_for_chat_phone()
                if len(choices) > 1:
                    self.active_booking_options_cache = choices
                    self.pending_existing_action = "cancel"
                    self.state = "ASK_EXISTING_BOOKING_PICK"
                    return self._respond(self._existing_booking_pick_prompt())
                if choices:
                    self._set_existing_booking_from_row(choices[0])
                if self.booking_repository and self.existing_appointment_id:
                    cancelled = self.booking_repository.cancel_appointment(
                        appointment_id=self.existing_appointment_id,
                        admin_id=self.admin_id,
                    )
                    if not cancelled:
                        self.state = "COMPLETED"
                        return self._respond(self._msg("existing_booking_cancel_failed"))
                self.existing_appointment_id = None
                self._reset_existing_booking_flags()
                self.state = "COMPLETED"
                return self._respond(self._msg("existing_booking_cancel_only_done"))
            if normalized in {"3", "rebook", "reschedule", "cancel and rebook", "yes"}:
                choices = self._active_booking_rows_for_chat_phone()
                if len(choices) > 1:
                    self.active_booking_options_cache = choices
                    self.pending_existing_action = "reschedule"
                    self.state = "ASK_EXISTING_BOOKING_PICK"
                    return self._respond(self._existing_booking_pick_prompt())
                if choices:
                    self._set_existing_booking_from_row(choices[0])
                self.in_reschedule_flow = True
                self.context.clinic_id = None
                self.context.clinic_name = None
                self.context.clinic_address = None
                self.context.appointment_date = None
                self.context.appointment_time = None
                self.clinic_options_cache = []
                self.date_options_cache = []
                self.time_options_cache = []
                self.time_hour_options_cache = []
                self.time_slot_options_cache = []
                self.time_window_labels_cache = []
                self.selected_time_hour = None
                self.selected_time_period = None
                self.state = "ASK_CLINIC"
                return self._respond(
                    self._msg("existing_booking_reschedule_start", clinic_name=self.existing_booking_clinic_name or "-")
                    + "\n"
                    + self._clinic_prompt()
                )
            if normalized in {"4", "another", "another person", "book another"}:
                choices = self._active_booking_rows_for_chat_phone()
                if len(choices) >= 2:
                    self.state = "ASK_MAX_ACTIVE_BOOKINGS_ACTION"
                    return self._respond(self._msg("max_active_bookings_reached") + "\n" + self._msg("max_active_bookings_actions"))
                self.existing_appointment_id = None
                self._reset_existing_booking_flags()
                self.booking_for_self = False
                self.context.patient_name = None
                self.context.phone_number = None
                self.context.clinic_id = None
                self.context.clinic_name = None
                self.context.clinic_address = None
                self.context.appointment_date = None
                self.context.appointment_time = None
                self.state = "ASK_NAME"
                return self._respond(self._msg("ask_name"))
            return self._respond(self._msg("existing_booking_choice_invalid"))

        if self.state == "ASK_MAX_ACTIVE_BOOKINGS_ACTION":
            normalized = lower.strip()
            choices = self._active_booking_rows_for_chat_phone()
            if normalized in {"1", "cancel"}:
                if not choices:
                    self.state = "ASK_EXISTING_BOOKING_ACTION"
                    return self._respond(self._msg("existing_booking_choice_again"))
                self.active_booking_options_cache = choices
                self.pending_existing_action = "cancel"
                self.state = "ASK_EXISTING_BOOKING_PICK"
                return self._respond(self._existing_booking_pick_prompt())
            if normalized in {"2", "reschedule", "rebook"}:
                if not choices:
                    self.state = "ASK_EXISTING_BOOKING_ACTION"
                    return self._respond(self._msg("existing_booking_choice_again"))
                self.active_booking_options_cache = choices
                self.pending_existing_action = "reschedule"
                self.state = "ASK_EXISTING_BOOKING_PICK"
                return self._respond(self._existing_booking_pick_prompt())
            if normalized in {"0", "back", "go back"}:
                self.state = "ASK_EXISTING_BOOKING_ACTION"
                return self._respond(self._msg("existing_booking_choice_again"))
            return self._respond(self._msg("max_active_bookings_invalid"))

        if self.state == "ASK_EXISTING_BOOKING_PICK":
            normalized = lower.strip()
            if not self.active_booking_options_cache:
                self.state = "ASK_EXISTING_BOOKING_ACTION"
                return self._respond(self._msg("existing_booking_choice_again"))
            if normalized in {"0", "back", "go back"}:
                self.state = "ASK_EXISTING_BOOKING_ACTION"
                self.active_booking_options_cache = []
                self.pending_existing_action = None
                return self._respond(self._msg("existing_booking_choice_again"))
            if normalized.isdigit():
                idx = int(normalized) - 1
                if 0 <= idx < len(self.active_booking_options_cache):
                    row = self.active_booking_options_cache[idx]
                    action = self.pending_existing_action
                    self.active_booking_options_cache = []
                    self.pending_existing_action = None
                    self._set_existing_booking_from_row(row)
                    if action == "cancel":
                        cancelled = False
                        if self.booking_repository and self.existing_appointment_id:
                            cancelled = self.booking_repository.cancel_appointment(
                                appointment_id=self.existing_appointment_id,
                                admin_id=self.admin_id,
                            )
                        if not cancelled:
                            self.state = "COMPLETED"
                            return self._respond(self._msg("existing_booking_cancel_failed"))
                        self.existing_appointment_id = None
                        self._reset_existing_booking_flags()
                        self.state = "COMPLETED"
                        return self._respond(self._msg("existing_booking_cancel_only_done"))
                    if action == "reschedule":
                        self.in_reschedule_flow = True
                        self.context.clinic_id = None
                        self.context.clinic_name = None
                        self.context.clinic_address = None
                        self.context.appointment_date = None
                        self.context.appointment_time = None
                        self.clinic_options_cache = []
                        self.date_options_cache = []
                        self.time_options_cache = []
                        self.time_hour_options_cache = []
                        self.time_slot_options_cache = []
                        self.time_window_labels_cache = []
                        self.selected_time_hour = None
                        self.selected_time_period = None
                        self.state = "ASK_CLINIC"
                        return self._respond(
                            self._msg("existing_booking_reschedule_start", clinic_name=self.existing_booking_clinic_name or "-")
                            + "\n"
                            + self._clinic_prompt()
                        )
            return self._respond(self._msg("existing_booking_pick_invalid"))

        if self.state == "ASK_AVAILABILITY_DETAILS":
            if is_booking_intent(lower) or is_restart_intent(lower):
                self.context = AppointmentContext()
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply)
                self.state = "ASK_BOOKING_FOR"
                return self._respond(self._msg("intent_ack") + "\n" + self._with_back(self._msg("ask_booking_for"), option_count=2))

            doctor_name = extract_doctor_name(text)
            if doctor_name:
                self.context.availability_doctor = doctor_name

            availability_date = extract_date(text)
            if not availability_date:
                availability_date = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="date",
                    text=text,
                )
            if availability_date:
                self.context.availability_date = availability_date

            if self.context.availability_date:
                return self._respond(self._availability_reply(self.context.availability_date))
            if not self.context.availability_doctor and not self.context.availability_date:
                return self._respond(self._msg("availability_ask"))
            return self._respond(self._msg("availability_ask_date"))

        # Legacy compatibility: older sessions may still carry removed states.
        # Route them to active runtime states instead of keeping dead branches.
        if self.state in {"ASK_PATIENT_TYPE", "ASK_AGE", "ASK_GENDER"}:
            self.state = "ASK_PHONE"
            return self._respond(self._with_back(self._ask_phone_prompt()))

        if self.state == "ASK_PHONE":
            chat_phone = self._normalize_phone(self.chat_phone_number or "")
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
                if self._is_telegram_channel():
                    return self._respond(self._with_back(self._msg("invalid_phone")))
                if not chat_phone:
                    return self._respond(self._with_back(self._msg("invalid_phone_same_missing")))
                self.context.phone_number = chat_phone
                if self.in_edit_flow:
                    self.in_edit_flow = False
                    self.state = "CONFIRM"
                    return self._respond(self._msg("change_ack", step="ASK_PHONE") + "\n" + self._msg("confirm_summary", **self._display_context()))
                auto = self._auto_select_single_clinic_after_phone()
                if auto:
                    return self._respond(self._msg("phone_ack", phone_number=self.context.phone_number) + "\n" + self._with_back(auto))
                self.state = "ASK_CLINIC"
                return self._respond(
                    self._msg("phone_ack", phone_number=self.context.phone_number) + "\n" + self._clinic_prompt()
                )

            phone = extract_phone(text)
            if not phone and normalized in different_number_markers:
                return self._respond(self._with_back(self._msg("invalid_phone")))
            # No LLM fallback here — phone must be rule-extractable (10 digits).
            # LLM adds 30-40s latency and cannot reliably fix genuinely invalid numbers.
            if not phone:
                return self._respond(self._with_back(self._msg("invalid_phone")))
            self.context.phone_number = phone
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_PHONE") + "\n" + self._msg("confirm_summary", **self._display_context()))
            auto = self._auto_select_single_clinic_after_phone()
            if auto:
                return self._respond(self._msg("phone_ack", phone_number=phone) + "\n" + self._with_back(auto))
            self.state = "ASK_CLINIC"
            return self._respond(self._msg("phone_ack", phone_number=phone) + "\n" + self._clinic_prompt())

        if self.state == "ASK_CLINIC":
            normalized = lower.strip()
            if self.scheduling_repository and self.doctor_id:
                if not self.clinic_options_cache:
                    self.clinic_options_cache = self._db_clinic_options()
                if not self.clinic_options_cache:
                    self.state = "INIT"
                    return self._respond(self._msg("no_clinic_available_restart"))
            selected = self._select_clinic(text, lower)
            if not selected:
                return self._respond(self._msg("invalid_clinic"))
            self.context.clinic_id = selected["id"]
            self.context.clinic_name = selected["name"]
            self.context.clinic_address = selected["address"]
            self.date_options_cache = []
            self.time_options_cache = []
            self.time_hour_options_cache = []
            self.time_slot_options_cache = []
            self.time_window_labels_cache = []
            self.selected_time_hour = None
            self.selected_time_period = None
            if self.in_edit_flow:
                # Clinic change affects slot pool, so re-collect date+time only.
                self.context.appointment_date = None
                self.context.appointment_time = None
            date_options = self._date_options()
            if not date_options:
                self.state = "ASK_CLINIC"
                return self._respond(self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic") + "\n" + self._clinic_prompt())
            self.state = "ASK_DATE"
            return self._respond(self._date_options_prompt(date_options))

        if self.state == "ASK_DATE":
            normalized = lower.strip()
            date_options = self._date_options()
            if not date_options:
                self.state = "ASK_CLINIC"
                return self._respond(self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic") + "\n" + self._clinic_prompt())
            if normalized.isdigit():
                index = int(normalized)
                if 1 <= index <= len(date_options):
                    parsed_date = date_options[index - 1]
                else:
                    return self._respond(
                        self._msg("invalid_date")
                        + "\n"
                        + self._date_options_prompt(date_options)
                    )
            elif normalized in {"today", "tomorrow"}:
                # Optional natural shortcuts if present in list.
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
                    return self._respond(
                        self._msg("invalid_date")
                        + "\n"
                        + self._date_options_prompt(date_options)
                    )
            else:
                return self._respond(
                    self._msg("invalid_date")
                    + "\n"
                    + self._date_options_prompt(date_options)
                )
            self.context.appointment_date = parsed_date
            self.time_options_cache = []
            self.time_hour_options_cache = []
            self.time_slot_options_cache = []
            self.time_window_labels_cache = []
            self.selected_time_hour = None
            self.selected_time_period = None
            self.state = "ASK_TIME"
            if not self._load_time_options(limit=60):
                self.state = "ASK_DATE"
                return self._respond(self._msg("no_time_available"))
            return self._respond(
                self._msg("date_ack", appointment_date=parsed_date)
                + "\n"
                + self._initial_time_prompt()
            )

        if self.state == "ASK_TIME":
            date_from_text = extract_date(text)
            if date_from_text and date_from_text != self.context.appointment_date:
                self.context.appointment_date = date_from_text
                self.time_options_cache = []
                self.time_hour_options_cache = []
                self.time_slot_options_cache = []
                self.time_window_labels_cache = []
                self.selected_time_hour = None
                self.selected_time_period = None
                if not self._load_time_options(limit=60):
                    self.state = "ASK_DATE"
                    return self._respond(self._msg("no_time_available"))
                return self._respond(
                    self._msg("date_ack", appointment_date=date_from_text)
                    + "\n"
                    + self._initial_time_prompt()
                )

            if not self._load_time_options(limit=60):
                self.state = "ASK_DATE"
                return self._respond(self._msg("no_time_available"))

            normalized = lower.strip()

            # Direct exact time is always allowed.
            parsed_time = extract_time(text)
            if not parsed_time:
                parsed_time = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="time",
                    text=text,
                )

            if parsed_time and self._is_available_time(parsed_time):
                self.context.appointment_time = parsed_time
                self.time_slot_options_cache = []
                self.time_window_labels_cache = []
                self.selected_time_hour = None
                self.selected_time_period = None
            else:
                # If total hours <= 4, show one-hour slots directly.
                # If total hours > 4, ask morning/afternoon/evening first.
                if not self.time_slot_options_cache:
                    if len(self.time_hour_options_cache) > 4:
                        if not self.selected_time_period:
                            period = self._resolve_time_period_choice(text, normalized)
                            if not period:
                                return self._respond(self._time_period_prompt())
                            if period == "__BACK__":
                                back_text = self._handle_go_back()
                                if back_text:
                                    return self._respond(back_text)
                                return self._respond(self._msg("invalid_time"))
                            self.selected_time_period = period
                        hour_choices = self._hour_slot_choices_for_period(self.selected_time_period)
                        if not hour_choices:
                            self.selected_time_period = None
                            return self._respond(self._time_period_prompt())
                    else:
                        hour_choices = self._hour_slot_choices_for_period(None)
                        if not hour_choices:
                            return self._respond(self._msg("no_time_available"))

                    self.time_window_labels_cache = [label for label, _ in hour_choices]
                    self.time_slot_options_cache = [actual_time for _, actual_time in hour_choices]
                    return self._respond(self._time_slot_prompt())

                selected_time: Optional[str] = None
                if normalized.isdigit():
                    idx = int(normalized) - 1
                    if 0 <= idx < len(self.time_slot_options_cache):
                        selected_time = self.time_slot_options_cache[idx]
                parsed_time = selected_time
                if not parsed_time:
                    return self._respond(self._msg("invalid_time") + "\n" + self._time_slot_prompt())
                if not self._is_available_time(parsed_time):
                    return self._respond(
                        self._msg("time_not_available", requested_time=parsed_time)
                        + "\n"
                        + self._time_slot_prompt()
                    )
                self.context.appointment_time = parsed_time
                self.time_slot_options_cache = []
                self.time_window_labels_cache = []
                self.selected_time_hour = None
                self.selected_time_period = None

            if self.in_reschedule_flow:
                self.state = "CONFIRM_RESCHEDULE"
                return self._respond(
                    self._msg(
                        "confirm_reschedule_summary",
                        old_date=self.existing_booking_old_date or "-",
                        old_time=self._format_time_for_display(self.existing_booking_old_time or "-"),
                        new_date=self.context.appointment_date or "-",
                        new_time=self._format_time_for_display(self.context.appointment_time or "-"),
                        clinic_name=self.context.clinic_name or "-",
                    )
                )
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_TIME") + "\n" + self._msg("confirm_summary", **self._display_context()))
            self.context.reason = None
            self.state = "CONFIRM"
            return self._respond(self._msg("time_ack", appointment_time=self._format_time_for_display(parsed_time)) + "\n" + self._msg("confirm_summary", **self._display_context()))

        if self.state == "CONFIRM_RESCHEDULE":
            normalized = lower.strip()
            if normalized == "1" or is_yes(lower):
                if not self.booking_repository or not self.existing_appointment_id:
                    self.in_reschedule_flow = False
                    self.state = "COMPLETED"
                    return self._respond(self._msg("existing_booking_cancel_failed"))
                result = self.booking_repository.reschedule_appointment_same_clinic(
                    appointment_id=self.existing_appointment_id,
                    new_date=self.context.appointment_date or "",
                    new_time=self.context.appointment_time or "",
                    new_clinic_id=int(self.context.clinic_id) if self.context.clinic_id else None,
                    admin_id=self.admin_id,
                )
                self.in_reschedule_flow = False
                self.state = "COMPLETED"
                if result.ok:
                    display_number = result.queue_number if result.queue_number is not None else result.appointment_id
                    return self._respond(
                        self._msg(
                            "reschedule_confirmed",
                            appointment_id=display_number,
                            clinic_name=self.context.clinic_name or "-",
                            appointment_date=self.context.appointment_date or "-",
                            appointment_time=self._format_time_for_display(self.context.appointment_time or "-"),
                        )
                    )
                return self._respond(self._msg("reschedule_failed"))
            if normalized == "2":
                self.state = "ASK_TIME"
                return self._respond(self._time_prompt_for_edit_flow())
            if normalized == "0" or is_no(lower):
                self.in_reschedule_flow = False
                self.state = "ASK_EXISTING_BOOKING_ACTION"
                return self._respond(self._msg("existing_booking_choice_again"))
            return self._respond(self._msg("confirm_reschedule_prompt"))

        if self.state == "ASK_REASON":
            reason_value = self._extract_reason_value(text, lower)
            if reason_value is None:
                return self._respond(self._msg("invalid_reason_option") + "\n" + self._msg("ask_reason"))
            if reason_value == "__ASK_OTHER__":
                return self._respond(self._msg("ask_reason_other"))
            self.context.reason = reason_value
            self.context.symptoms = None
            if self.in_edit_flow:
                self.in_edit_flow = False
            self.state = "CONFIRM"
            return self._respond(self._msg("reason_ack") + "\n" + self._msg("confirm_summary", **self._display_context()))

        if self.state == "ASK_SYMPTOMS":
            # Legacy compatibility: older sessions may still enter ASK_SYMPTOMS.
            self.context.symptoms = text if len(text) >= 3 else None
            if self.in_edit_flow:
                self.in_edit_flow = False
            self.state = "CONFIRM"
            return self._respond(self._msg("confirm_summary", **self._display_context()))

        if self.state == "CONFIRM":
            confirm_intent = self._detect_confirm_intent(text)
            if confirm_intent == "yes":
                self.state = "COMPLETED"
                confirmed = self._msg("confirmed", **self._display_context())
                persist_note = self._persist_confirmed_appointment()
                if persist_note:
                    confirmed = persist_note + "\n\n" + confirmed
                return self._respond(confirmed)
            if confirm_intent == "back":
                back_text = self._handle_go_back()
                if back_text:
                    return self._respond(back_text)
            if confirm_intent in {"no", "change"}:
                reroute_state = resolve_change_target(lower)
                if not reroute_state and confirm_intent == "change":
                    reroute_state = llm_change_target(
                        llm_client=self.llm_client,
                        enable_llm_polish=self.enable_llm_polish,
                        text=text,
                    )
                if reroute_state:
                    self.state = reroute_state
                    if reroute_state == "ASK_CLINIC":
                        return self._respond(self._msg("change_ack") + "\n" + self._clinic_prompt())
                    if reroute_state == "ASK_TIME":
                        return self._respond(self._msg("change_ack") + "\n" + self._time_prompt_for_edit_flow())
                    return self._respond(self._msg("change_ack", step=reroute_state))
                self.state = "ASK_CHANGE_FIELD"
                return self._respond(self._with_back(self._msg("ask_change_field"), option_count=5))
            return self._respond(self._msg("confirm_prompt"))

        if self.state == "ASK_CHANGE_FIELD":
            if lower.strip() == "0":
                back_text = self._handle_go_back()
                if back_text:
                    return self._respond(back_text)
            reroute_state = resolve_change_target(lower)
            if not reroute_state:
                reroute_state = self._change_state_from_option(lower)
            if not reroute_state:
                reroute_state = llm_change_target(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    text=text,
                )
            if not reroute_state:
                return self._respond(
                    self._msg("invalid_change_field")
                    + "\n"
                    + self._with_back(self._msg("ask_change_field"), option_count=5)
                )
            self.in_edit_flow = True
            self.state = reroute_state
            if reroute_state == "ASK_CLINIC":
                return self._respond(self._msg("change_ack") + "\n" + self._clinic_prompt())
            if reroute_state == "ASK_TIME":
                return self._respond(self._msg("change_ack") + "\n" + self._time_prompt_for_edit_flow())
            return self._respond(self._msg("change_ack", step=reroute_state))

        if self.state == "COMPLETED":
            existing_reply = self._existing_booking_entry_response()
            if existing_reply:
                return self._respond(existing_reply)
            if is_booking_intent(lower) or is_restart_intent(lower):
                self._reset_all(cancelled=False)
                existing_reply = self._existing_booking_entry_response()
                if existing_reply:
                    return self._respond(existing_reply)
                self.state = "ASK_BOOKING_FOR"
                return self._respond(self._with_back(self._msg("ask_booking_for"), option_count=2))
            return self._respond(self._msg("completed_hint"))

        self._reset_all(cancelled=False)
        self.state = "ASK_NAME"
        return self._respond(self._msg("ask_name"))

    def _normalize_option_input_for_state(self, text: str, lower: str) -> tuple[str, str]:
        option_states = {
            "ASK_BOOKING_FOR",
            "ASK_EXISTING_BOOKING_ACTION",
            "ASK_MAX_ACTIVE_BOOKINGS_ACTION",
            "ASK_EXISTING_BOOKING_PICK",
            "ASK_APPOINTMENT_MODE",
            "ASK_CLINIC",
            "ASK_DATE",
            "ASK_TIME",
            "ASK_REASON",
            "CONFIRM_RESCHEDULE",
            "CONFIRM",
            "ASK_CHANGE_FIELD",
        }
        if self.state not in option_states:
            return text, lower

        normalized = re.sub(r"\s+", " ", (lower or "").strip())
        if not normalized:
            return text, lower

        number_map = {
            "0": "0",
            "zero": "0",
            "०": "0",
            "1": "1",
            "one": "1",
            "won": "1",
            "ek": "1",
            "एक": "1",
            "१": "1",
            "2": "2",
            "two": "2",
            "too": "2",
            "to": "2",
            "do": "2",
            "दो": "2",
            "२": "2",
            "3": "3",
            "three": "3",
            "tree": "3",
            "teen": "3",
            "तीन": "3",
            "३": "3",
            "4": "4",
            "four": "4",
            "for": "4",
            "char": "4",
            "चार": "4",
            "४": "4",
            "5": "5",
            "five": "5",
            "paanch": "5",
            "पांच": "5",
            "५": "5",
        }
        if normalized in number_map:
            mapped = number_map[normalized]
            return mapped, mapped

        prefixed = re.fullmatch(r"(?:option|number|choice|no\.?)\s+(.+)", normalized)
        if prefixed:
            token = prefixed.group(1).strip()
            if token in number_map:
                mapped = number_map[token]
                return mapped, mapped
        return text, lower

    def _respond(self, base_text: str, allow_polish: bool = True) -> str:
        return base_text

    def _persist_confirmed_appointment(self) -> str:
        if not self.booking_repository:
            return ""
        # Persist Telegram chat user id separately for Telegram channel lookups.
        if self._is_telegram_channel():
            setattr(self.context, "chat_user_id", self.chat_phone_number or "")
        else:
            setattr(self.context, "chat_user_id", None)
        setattr(self.context, "booking_for_self", self.booking_for_self)
        try:
            try:
                result = self.booking_repository.save_confirmed_appointment(
                    self.context,
                    admin_id=self.admin_id,
                    doctor_id=self.doctor_id,
                )
            except TypeError:
                result = self.booking_repository.save_confirmed_appointment(
                    self.context,
                    admin_id=self.admin_id,
                )
        except Exception as exc:
            LOGGER.warning("DB persistence failed: %s", exc)
            return self._msg("db_save_failed")
        if result.ok:
            display_number = result.queue_number if result.queue_number is not None else result.appointment_id
            return self._msg("db_save_ok", appointment_id=display_number)
        LOGGER.warning("DB persistence returned failure: %s", result.message)
        return self._msg("db_save_failed")

    def _detect_confirm_intent(self, text: str) -> str:
        lower = text.lower().strip()
        if lower == "1" or is_yes(lower):
            return "yes"
        if lower == "0":
            return "back"
        if lower == "2":
            return "no"
        if is_no(lower):
            if any(token in lower for token in ["change", "edit", "modify"]):
                return "change"
            return "no"
        return llm_detect_confirm_intent(
            llm_client=self.llm_client,
            enable_llm_polish=self.enable_llm_polish,
            text=text,
        )

    def _reset_all(self, cancelled: bool) -> None:
        self.context = AppointmentContext()
        self.state = "CANCELLED" if cancelled else "INIT"
        self.init_unclear_count = 0
        self.in_edit_flow = False
        self.known_patient_name = None
        self.clinic_options_cache = []
        self.date_options_cache = []
        self.time_options_cache = []
        self.time_hour_options_cache = []
        self.time_slot_options_cache = []
        self.time_window_labels_cache = []
        self.selected_time_hour = None
        self.selected_time_period = None
        self.booking_for_self = None
        self.existing_appointment_id = None
        self._reset_existing_booking_flags()
        self.active_booking_options_cache = []
        self.pending_existing_action = None
        if not cancelled and self.mixed_response_language.lower() == "auto":
            self.language_locked = False
            self.response_language = "en"
            self.language_turn_count = 0

    def _reset_existing_booking_flags(self) -> None:
        self.existing_booking_clinic_id = None
        self.existing_booking_clinic_name = None
        self.existing_booking_doctor_id = None
        self.existing_booking_old_date = None
        self.existing_booking_old_time = None
        self.in_reschedule_flow = False
        self.active_booking_options_cache = []
        self.pending_existing_action = None

    def _msg(self, key: str, **kwargs: object) -> str:
        return get_message(self.response_language, key, **kwargs)

    def _with_back(self, text: str, option_count: Optional[int] = None) -> str:
        if option_count is not None and option_count >= 1:
            return text + "\n" + self._msg("go_back_hint")
        return text + "\n" + self._msg("go_back_hint")

    @staticmethod
    def _is_go_back(lower_text: str) -> bool:
        val = (lower_text or "").strip().lower()
        return val in {"go back", "back", "previous", "prev", "menu", "0", "c"}

    def _handle_go_back(self) -> Optional[str]:
        if self.state == "ASK_BOOKING_FOR":
            self.state = "INIT"
            return self._msg("no_intent")
        if self.state == "ASK_NAME":
            self.state = "ASK_BOOKING_FOR"
            return self._with_back(self._msg("ask_booking_for"), option_count=2)
        if self.state == "ASK_PHONE":
            self.state = "ASK_NAME"
            return self._with_back(self._msg("ask_name"))
        if self.state == "ASK_CLINIC":
            if self.booking_for_self and self.known_patient_name:
                # Known patient self-booking: name+phone were auto-filled and skipped,
                # so go back to ASK_BOOKING_FOR (the last state the patient actually saw).
                self.state = "ASK_BOOKING_FOR"
                return self._with_back(self._msg("ask_booking_for"), option_count=2)
            # Normal path: phone step was actually visited, go back to it.
            self.state = "ASK_PHONE"
            return self._with_back(self._ask_phone_prompt())
        if self.state == "ASK_DATE":
            self.state = "ASK_CLINIC"
            return self._clinic_prompt()
        if self.state == "ASK_TIME":
            self.state = "ASK_DATE"
            dates = self._date_options()
            if not dates:
                return self._with_back(self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic"))
            return self._date_options_prompt(dates)
        if self.state == "ASK_CHANGE_FIELD":
            self.state = "CONFIRM"
            return self._msg("confirm_summary", **self._display_context())
        if self.state == "CONFIRM":
            self.state = "ASK_TIME"
            return self._with_back(self._initial_time_prompt())
        return None

    def _welcome_greeting(self) -> str:
        # known_patient_name is already hydrated by _hydrate_known_patient_name()
        # which is called unconditionally at the top of handle() every turn.
        # We only need to fetch the doctor display name here.
        self._ensure_actor_defaults()
        doctor_name = "Doctor"
        if self.booking_repository:
            try:
                doctor_from_db = self.booking_repository.get_doctor_display_name(
                    doctor_id=self.doctor_id,
                    admin_id=self.admin_id,
                )
                if doctor_from_db:
                    doctor_name = doctor_from_db
                elif self.doctor_id is not None:
                    doctor_from_db = self.booking_repository.get_doctor_display_name(
                        doctor_id=self.doctor_id,
                        admin_id=None,
                    )
                    if doctor_from_db:
                        doctor_name = doctor_from_db
            except Exception:
                pass
        if self.known_patient_name:
            return self._msg(
                "welcome_known_patient",
                doctor_name=doctor_name,
                patient_name=self.known_patient_name,
            )
        return self._msg("welcome_new_patient", doctor_name=doctor_name)

    def _is_abusive_message(self, text: str, lower: str, allow_llm: bool = False) -> bool:
        normalized_latin = re.sub(r"[^a-z0-9]+", " ", lower).strip()
        padded = f" {normalized_latin} "
        for term in ABUSE_TERMS_EN:
            if f" {term} " in padded:
                return True
        for term in ABUSE_TERMS_HINGLISH:
            if f" {term} " in padded:
                return True
        for term in ABUSE_TERMS_HI:
            if term in text:
                return True
        if not allow_llm:
            return False
        return llm_detect_abuse(
            llm_client=self.llm_client,
            enable_llm_polish=self.enable_llm_polish,
            text=text,
        )

    def _existing_booking_entry_response(self) -> Optional[str]:
        if not self.booking_repository:
            return None
        self._ensure_actor_defaults()
        rows = self._active_booking_rows_for_chat_phone()
        if not rows:
            return None
        self._set_existing_booking_from_row(rows[0])
        self.state = "ASK_EXISTING_BOOKING_ACTION"
        display_number = rows[0].get("booking_number")
        if display_number is None:
            display_number = rows[0]["appointment_id"]
        return self._msg(
            "existing_booking_found",
            appointment_id=display_number,
            appointment_date=rows[0].get("slot_date") or "-",
            appointment_time=self._format_time_for_display(rows[0].get("slot_time") or "-"),
            clinic_name=rows[0].get("clinic_name") or "-",
        )

    def _set_existing_booking_from_row(self, row: dict) -> None:
        self.existing_appointment_id = int(row["appointment_id"])
        self.existing_booking_clinic_id = str(row.get("clinic_id") or "")
        self.existing_booking_clinic_name = str(row.get("clinic_name") or "")
        self.existing_booking_doctor_id = int(row.get("doctor_id") or 0) or None
        self.existing_booking_old_date = str(row.get("slot_date") or "")
        self.existing_booking_old_time = str(row.get("slot_time") or "")

    def _active_booking_rows_for_chat_phone(self) -> list[dict]:
        if not self.booking_repository:
            return []
        raw_chat_id = (self.chat_phone_number or "").strip()
        chat_phone = self._normalize_phone(raw_chat_id)
        try:
            if self._is_telegram_channel():
                if not raw_chat_id:
                    return []
                return self.booking_repository.list_active_appointments_by_chat_user_id(
                    chat_user_id=raw_chat_id,
                    admin_id=self.admin_id,
                    doctor_id=self.doctor_id,
                    limit=10,
                )
            if not chat_phone:
                return []
            return self.booking_repository.list_active_appointments_by_phone_number(
                phone_number=chat_phone,
                admin_id=self.admin_id,
                doctor_id=self.doctor_id,
                limit=10,
            )
        except TypeError:
            try:
                row = self.booking_repository.find_active_appointment_by_phone_number(
                    phone_number=chat_phone,
                    admin_id=self.admin_id,
                )
            except Exception:
                row = None
            return [row] if row else []
        except Exception:
            return []

    def _existing_booking_pick_prompt(self) -> str:
        if not self.active_booking_options_cache:
            return self._msg("existing_booking_choice_again")
        lines = [self._msg("existing_booking_pick_header")]
        for idx, row in enumerate(self.active_booking_options_cache, start=1):
            display_number = row.get("booking_number")
            if display_number is None:
                display_number = row.get("appointment_id")
            lines.append(
                f"{idx}. {row.get('clinic_name') or '-'} | {row.get('slot_date') or '-'} | "
                f"{self._format_time_for_display(str(row.get('slot_time') or '-'))} | Booking Number: {display_number}"
            )
        lines.append(self._msg("go_back_hint"))
        choice_numbers = ", ".join(str(i) for i in range(1, len(self.active_booking_options_cache) + 1))
        lines.append(self._msg("reply_with_numbers", numbers=f"{choice_numbers}, 0"))
        return "\n".join(lines)

    def _update_response_language(self, lower: str) -> None:
        (
            self.response_language,
            self.language_locked,
            self.language_turn_count,
        ) = update_response_language(
            lower=lower,
            mixed_response_language=self.mixed_response_language,
            response_language=self.response_language,
            language_locked=self.language_locked,
            language_turn_count=self.language_turn_count,
            llm_client=self.llm_client,
            enable_llm_fallback=False,
        )

    def _select_clinic(self, text: str, lower: str) -> Optional[dict]:
        self._ensure_actor_defaults()
        if self.scheduling_repository and self.doctor_id:
            if not self.clinic_options_cache:
                self.clinic_options_cache = self._db_clinic_options()
            normalized = lower.strip()
            if normalized.isdigit():
                idx = int(normalized) - 1
                if 0 <= idx < len(self.clinic_options_cache):
                    return self.clinic_options_cache[idx]
            for clinic in self.clinic_options_cache:
                name = clinic["name"].lower()
                if name in lower or any(token in lower for token in name.split()):
                    return clinic
            return None
        return None

    def _load_time_options(self, limit: int = 60) -> bool:
        self._ensure_actor_defaults()
        if (
            not self.scheduling_repository
            or not self.doctor_id
            or not self.context.clinic_id
            or not self.context.appointment_date
        ):
            self.time_options_cache = []
            return False
        self.time_options_cache = self.scheduling_repository.list_available_times(
            doctor_id=self.doctor_id,
            clinic_id=int(self.context.clinic_id),
            slot_date=self.context.appointment_date,
            admin_id=self.admin_id,
            limit=limit,
        )
        self.time_hour_options_cache = []
        self.time_window_labels_cache = []
        self.selected_time_period = None
        seen: set[str] = set()
        for hhmm in self.time_options_cache:
            hour = hhmm.split(":")[0]
            if hour not in seen:
                seen.add(hour)
                self.time_hour_options_cache.append(hour)
        return len(self.time_options_cache) > 0

    def _time_hour_prompt(self) -> str:
        if not self.time_hour_options_cache:
            return self._msg("no_time_available")
        lines = [self._msg("ask_time_hour_options")]
        for index, hour in enumerate(self.time_hour_options_cache, start=1):
            label = self._format_time_for_display(f"{int(hour):02d}:00")
            lines.append(f"{index}. {label}")
        lines.append(self._reply_with_prompt(len(self.time_hour_options_cache)))
        lines.append("Or type exact time (e.g., 16:20).")
        return "\n".join(lines)

    def _initial_time_prompt(self) -> str:
        if len(self.time_hour_options_cache) > 4:
            return self._time_period_prompt()
        hour_choices = self._hour_slot_choices_for_period(None)
        if not hour_choices:
            return self._msg("no_time_available")
        self.time_window_labels_cache = [label for label, _ in hour_choices]
        self.time_slot_options_cache = [actual_time for _, actual_time in hour_choices]
        return self._time_slot_prompt()

    def _time_period_prompt(self) -> str:
        periods = self._available_periods()
        if not periods:
            return self._msg("no_time_available")
        labels = {
            "morning": "Morning",
            "afternoon": "Afternoon",
            "evening": "Evening",
        }
        lines = ["Please choose preferred time period:"]
        for idx, period in enumerate(periods, start=1):
            lines.append(f"{idx}. {labels.get(period, period.title())}")
        lines.append(self._msg("go_back_hint"))
        lines.append(self._reply_with_prompt(len(periods)))
        return "\n".join(lines)

    def _resolve_time_period_choice(self, text: str, normalized: str) -> Optional[str]:
        periods = self._available_periods()
        if not periods:
            return None
        if normalized in {"0", "back", "go back"}:
            return "__BACK__"
        if normalized.isdigit():
            idx = int(normalized) - 1
            if 0 <= idx < len(periods):
                return periods[idx]
        lower = (text or "").strip().lower()
        for period in periods:
            if period in lower:
                return period
        return None

    def _available_periods(self) -> list[str]:
        periods: list[str] = []
        for hour_str in self.time_hour_options_cache:
            p = self._hour_to_period(int(hour_str))
            if p not in periods:
                periods.append(p)
        return periods

    def _hour_slot_choices_for_period(self, period: Optional[str]) -> list[tuple[str, str]]:
        hours = [int(h) for h in self.time_hour_options_cache]
        if period:
            hours = [h for h in hours if self._hour_to_period(h) == period]
        hours = sorted(hours)
        choices: list[tuple[str, str]] = []
        for h in hours:
            hour_times = [t for t in self.time_options_cache if t.startswith(f"{h:02d}:")]
            if not hour_times:
                continue
            start = f"{h:02d}:00"
            end = f"{(h + 1) % 24:02d}:00"
            label = f"{self._format_time_for_display(start)} - {self._format_time_for_display(end)}"
            choices.append((label, hour_times[0]))
        return choices

    @staticmethod
    def _hour_to_period(hour: int) -> str:
        if hour < 12:
            return "morning"
        if hour < 16:
            return "afternoon"
        return "evening"

    def _resolve_hour_choice(self, text: str, normalized: str) -> Optional[str]:
        if not self.time_hour_options_cache:
            return None
        if normalized.isdigit():
            numeric = int(normalized)
            # Option index selection.
            idx = numeric - 1
            if 0 <= idx < len(self.time_hour_options_cache):
                return self.time_hour_options_cache[idx]
            # Direct hour (24h), e.g. "16".
            direct = f"{numeric:02d}"
            if direct in self.time_hour_options_cache:
                return direct
            # Common 12h shorthand, e.g. "4" -> 16 if available.
            if 1 <= numeric <= 12:
                pm = f"{(numeric % 12) + 12:02d}" if numeric != 12 else "12"
                am = f"{numeric % 12:02d}" if numeric != 12 else "00"
                if pm in self.time_hour_options_cache:
                    return pm
                if am in self.time_hour_options_cache:
                    return am
        parsed = extract_time(text)
        if parsed:
            hour = parsed.split(":")[0]
            if hour in self.time_hour_options_cache:
                return hour
        return None

    def _nearest_slots_for_hour(self, hour: str, limit: int = 3) -> list[str]:
        if not self.time_options_cache:
            return []
        try:
            target = int(hour) * 60
        except ValueError:
            return self.time_options_cache[:limit]

        def to_minutes(hhmm: str) -> int:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)

        ordered = sorted(self.time_options_cache, key=lambda t: (abs(to_minutes(t) - target), to_minutes(t)))
        result = ordered[: max(1, limit)]
        # Keep chronological order in the final display.
        return sorted(result, key=to_minutes)

    def _window_choices_for_hour(self, hour: str) -> list[tuple[str, str]]:
        if not self.time_options_cache:
            return []
        try:
            hour_int = int(hour)
        except ValueError:
            return []

        hour_times = [t for t in self.time_options_cache if t.startswith(f"{hour_int:02d}:")]
        if not hour_times:
            return []

        first_half = [t for t in hour_times if int(t.split(":")[1]) < 30]
        second_half = [t for t in hour_times if int(t.split(":")[1]) >= 30]

        choices: list[tuple[str, str]] = []
        if first_half:
            start = f"{hour_int:02d}:00"
            end = f"{hour_int:02d}:30"
            label = f"{self._format_time_for_display(start)} - {self._format_time_for_display(end)}"
            choices.append((label, first_half[0]))
        if second_half:
            start = f"{hour_int:02d}:30"
            end = f"{(hour_int + 1) % 24:02d}:00"
            label = f"{self._format_time_for_display(start)} - {self._format_time_for_display(end)}"
            choices.append((label, second_half[0]))
        return choices

    def _time_slot_prompt(self) -> str:
        if not self.time_slot_options_cache:
            return self._msg("no_time_available")
        lines = [self._msg("choose_slot_header")]
        if self.time_window_labels_cache:
            for idx, label in enumerate(self.time_window_labels_cache, start=1):
                lines.append(f"{idx}. {label}")
        else:
            for idx, hhmm in enumerate(self.time_slot_options_cache, start=1):
                lines.append(f"{idx}. {self._format_time_for_display(hhmm)}")
        lines.append(self._msg("go_back_hint"))
        lines.append(self._reply_with_prompt(len(self.time_slot_options_cache)))
        return "\n".join(lines)

    def _time_prompt_for_edit_flow(self) -> str:
        if not self.context.appointment_date:
            dates = self._date_options()
            if not dates:
                return self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic")
            self.state = "ASK_DATE"
            return self._date_options_prompt(dates)
        if not self._load_time_options(limit=60):
            self.state = "ASK_DATE"
            return self._msg("no_time_available")
        return self._initial_time_prompt()

    def _normalize_phone(self, value: str) -> Optional[str]:
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[-10:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[-10:]
        if len(digits) == 10:
            return digits
        return None

    def _is_telegram_channel(self) -> bool:
        raw = (self.chat_phone_number or "").strip().lower()
        return raw.startswith("telegram:")

    def _is_telegram_start_command(self, lower_text: str) -> bool:
        if not self._is_telegram_channel():
            return False
        normalized = (lower_text or "").strip().lower()
        return normalized in {"/start", "start"}

    def _ask_phone_prompt(self) -> str:
        if self._is_telegram_channel():
            return self._msg("ask_phone_telegram")
        return self._msg("ask_phone")

    def _date_options(self) -> list[str]:
        self._ensure_actor_defaults()
        if self.scheduling_repository and self.doctor_id and self.context.clinic_id:
            if not self.date_options_cache:
                accept_days = self.scheduling_repository.doctor_accept_days(
                    doctor_id=self.doctor_id,
                    admin_id=self.admin_id,
                )
                limit = max(1, min(14, accept_days + 1))
                self.date_options_cache = self.scheduling_repository.list_available_dates(
                    doctor_id=self.doctor_id,
                    clinic_id=int(self.context.clinic_id),
                    admin_id=self.admin_id,
                    limit=limit,
                )
            return list(self.date_options_cache)
        return []

    def _extract_reason_value(self, text: str, lower: str) -> Optional[str]:
        reason_map = {
            "1": "Fever",
            "2": "Headache",
            "3": "Stomach pain",
            "4": "Cold",
        }
        separators = [",", "/", "|", ";"]
        normalized = lower
        for sep in separators:
            normalized = normalized.replace(sep, " ")
        tokens = [token.strip() for token in normalized.split() if token.strip()]

        selected: list[str] = []
        for token in tokens:
            if token in reason_map and reason_map[token] not in selected:
                selected.append(reason_map[token])

        keyword_map = {
            "fever": "Fever",
            "headache": "Headache",
            "stomach": "Stomach pain",
            "cold": "Cold",
        }
        for key, value in keyword_map.items():
            if key in lower and value not in selected:
                selected.append(value)

        if "5" in tokens or "other" in tokens:
            free_text = text
            for marker in ["other", "5"]:
                free_text = free_text.replace(marker, "")
                free_text = free_text.replace(marker.upper(), "")
            free_text = free_text.strip(" :-,")
            if free_text:
                selected.append(free_text)
            elif selected:
                return ", ".join(selected)
            else:
                return "__ASK_OTHER__"

        if selected:
            return ", ".join(selected)
        if len(text.strip()) >= 3:
            return text.strip()
        return None

    def _suggested_slots(self) -> list[str]:
        self._ensure_actor_defaults()
        if (
            self.scheduling_repository
            and self.doctor_id
            and self.context.clinic_id
            and self.context.appointment_date
        ):
            self.time_options_cache = self.scheduling_repository.list_available_times(
                doctor_id=self.doctor_id,
                clinic_id=int(self.context.clinic_id),
                slot_date=self.context.appointment_date,
                admin_id=self.admin_id,
                limit=3,
            )
            if self.time_options_cache:
                return self.time_options_cache[:3]
            return []
        return []

    def _is_available_time(self, parsed_time: str) -> bool:
        return parsed_time in self.time_options_cache

    def _change_state_from_option(self, lower: str) -> Optional[str]:
        options = {
            "1": "ASK_NAME",
            "2": "ASK_PHONE",
            "3": "ASK_CLINIC",
            "4": "ASK_DATE",
            "5": "ASK_TIME",
        }
        normalized = lower.strip()
        return options.get(normalized)

    def _ensure_actor_defaults(self) -> None:
        if self.admin_id is None and self.booking_repository:
            try:
                self.admin_id = self.booking_repository.default_admin_id()
            except Exception:
                self.admin_id = None
        if self.doctor_id is None and self.scheduling_repository:
            try:
                if self.bot_whatsapp_number:
                    marker = "telegram_username:"
                    if self.bot_whatsapp_number.startswith(marker):
                        username = self.bot_whatsapp_number[len(marker) :].strip()
                        self.doctor_id = self.scheduling_repository.default_doctor_id_by_username(
                            username=username,
                            admin_id=self.admin_id,
                        )
                    else:
                        self.doctor_id = self.scheduling_repository.default_doctor_id_by_phone(
                            phone_number=self.bot_whatsapp_number,
                            admin_id=self.admin_id,
                        )
                # Safety: when channel number is configured but does not match any doctor,
                # do not silently route to another doctor.
                elif self.doctor_id is None:
                    self.doctor_id = self.scheduling_repository.default_doctor_id(admin_id=self.admin_id)
            except Exception:
                self.doctor_id = None

    def _handle_init_booking_prefill(self, *, text: str, lower: str) -> Optional[str]:
        """
        First-message-only prefill:
        - LLM-first extraction for richer free-text inputs
        - Rule-based fallback to keep deterministic behavior
        - Route user directly to the next missing state
        """
        self._ensure_actor_defaults()
        self._hydrate_known_patient_name()
        self._detect_booking_actor_from_text(lower)
        self._apply_init_booking_prefill_from_llm(text)
        self._apply_init_booking_prefill_from_rules(text, lower)
        self._apply_clinic_prefill_from_text(text, lower)
        return self._route_after_init_prefill()

    def _hydrate_known_patient_name(self) -> None:
        if self.known_patient_name:
            return
        if not self.booking_repository:
            return
        try:
            if self._is_telegram_channel():
                raw_chat = (self.chat_phone_number or "").strip()
                if not raw_chat:
                    return
                patient_name = self.booking_repository.find_patient_name_by_chat_user_id(
                    chat_user_id=raw_chat,
                    admin_id=self.admin_id,
                    doctor_id=self.doctor_id,
                )
            else:
                chat_phone = self._normalize_phone(self.chat_phone_number or "")
                if not chat_phone:
                    return
                patient_name = self.booking_repository.find_patient_name_by_phone_number(
                    phone_number=chat_phone,
                    admin_id=self.admin_id,
                    doctor_id=self.doctor_id,
                )
            if patient_name:
                self.known_patient_name = patient_name
        except Exception:
            return

    def _detect_booking_actor_from_text(self, lower: str) -> None:
        normalized = (lower or "").strip().lower()
        other_markers = (
            "for my mother",
            "for my father",
            "for my wife",
            "for my husband",
            "for my son",
            "for my daughter",
            "for another",
            "another person",
            "someone else",
        )
        self_markers = (
            "for me",
            "myself",
            "self",
            "for my appointment",
        )
        if any(marker in normalized for marker in other_markers):
            self.booking_for_self = False
            return
        if any(marker in normalized for marker in self_markers):
            self.booking_for_self = True

    def _apply_init_booking_prefill_from_llm(self, text: str) -> None:
        prefill = llm_extract_booking_prefill(
            llm_client=self.llm_client,
            enable_llm_polish=self.enable_llm_polish,
            text=text,
        )
        if not prefill:
            return
        patient_name = prefill.get("patient_name")
        appointment_date = prefill.get("appointment_date")
        appointment_time = prefill.get("appointment_time")
        clinic_name = prefill.get("clinic_name")
        booking_for = prefill.get("booking_for")

        if patient_name and not self.context.patient_name:
            self.context.patient_name = patient_name
        if appointment_date and not self.context.appointment_date:
            self.context.appointment_date = appointment_date
        if appointment_time and not self.context.appointment_time:
            self.context.appointment_time = appointment_time
        if clinic_name and not self.context.clinic_name:
            self.context.clinic_name = clinic_name
        if booking_for == "self":
            self.booking_for_self = True
        elif booking_for == "other":
            self.booking_for_self = False

    def _apply_init_booking_prefill_from_rules(self, text: str, lower: str) -> None:
        if not self.context.patient_name:
            name = extract_name(text)
            if name:
                self.context.patient_name = name

        if not self.context.appointment_date:
            parsed_date = extract_date(text)
            if not parsed_date:
                parsed_date = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="date",
                    text=text,
                )
            if parsed_date:
                self.context.appointment_date = parsed_date

        if not self.context.appointment_time:
            parsed_time = extract_time(text)
            if not parsed_time:
                parsed_time = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="time",
                    text=text,
                )
            if parsed_time:
                self.context.appointment_time = parsed_time

        if self.booking_for_self is True:
            if self.known_patient_name and not self.context.patient_name:
                self.context.patient_name = self.known_patient_name
            if not self.context.phone_number and not self._is_telegram_channel():
                chat_phone = self._normalize_phone(self.chat_phone_number or "")
                if chat_phone:
                    self.context.phone_number = chat_phone

    def _apply_clinic_prefill_from_text(self, text: str, lower: str) -> None:
        if self.context.clinic_id:
            return
        clinic = self._select_clinic(text, lower)
        if not clinic and self.context.clinic_name:
            clinic_lower = self.context.clinic_name.lower()
            if not self.clinic_options_cache:
                self.clinic_options_cache = self._db_clinic_options() if self.scheduling_repository and self.doctor_id else []
            for option in self.clinic_options_cache:
                name = str(option.get("name") or "").lower()
                if clinic_lower and clinic_lower in name:
                    clinic = option
                    break
        if clinic:
            self.context.clinic_id = clinic["id"]
            self.context.clinic_name = clinic["name"]
            self.context.clinic_address = clinic["address"]
            self.date_options_cache = []
            self.time_options_cache = []
            self.time_hour_options_cache = []
            self.time_slot_options_cache = []
            self.time_window_labels_cache = []
            self.selected_time_hour = None
            self.selected_time_period = None

    def _route_after_init_prefill(self) -> Optional[str]:
        # If actor is unknown, keep current behavior and ask booking-for.
        if self.booking_for_self is None:
            return None

        if not self.context.patient_name:
            self.state = "ASK_NAME"
            return self._with_back(self._msg("ask_name"))

        if not self.context.phone_number:
            self.state = "ASK_PHONE"
            return self._with_back(self._ask_phone_prompt())

        if not self.context.clinic_id:
            self.state = "ASK_CLINIC"
            return self._clinic_prompt()

        if not self.context.appointment_date:
            date_options = self._date_options()
            if not date_options:
                self.state = "ASK_CLINIC"
                return self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic") + "\n" + self._clinic_prompt()
            self.state = "ASK_DATE"
            return self._date_options_prompt(date_options)

        if not self._load_time_options(limit=60):
            self.state = "ASK_DATE"
            return self._msg("no_time_available")

        if self.context.appointment_time:
            parsed_time = extract_time(self.context.appointment_time)
            if parsed_time and self._is_available_time(parsed_time):
                self.context.appointment_time = parsed_time
                self.state = "CONFIRM"
                return self._msg("confirm_summary", **self._display_context())
            self.context.appointment_time = None

        self.state = "ASK_TIME"
        return self._initial_time_prompt()

    def _db_clinic_options(self) -> list[dict]:
        if not self.scheduling_repository or not self.doctor_id:
            return []
        clinics = self.scheduling_repository.list_clinics_for_doctor(
            doctor_id=self.doctor_id,
            admin_id=self.admin_id,
            limit=10,
        )
        options: list[dict] = []
        for index, clinic in enumerate(clinics, start=1):
            options.append(
                {
                    "id": str(clinic.clinic_id),
                    "ordinal": str(index),
                    "name": clinic.clinic_name,
                    "address": clinic.location,
                    "today_slots": clinic.today_slots,
                }
            )
        return options

    def _clinic_prompt(self) -> str:
        self._ensure_actor_defaults()
        if self.scheduling_repository and self.doctor_id:
            self.clinic_options_cache = self._db_clinic_options()
            if self.clinic_options_cache:
                lines = [self._msg("ask_clinic_header")]
                for clinic in self.clinic_options_cache[:10]:
                    lines.append(f"{clinic['ordinal']}. {clinic['name']} | {clinic['address']}")
                option_count = len(self.clinic_options_cache[:10])
                lines.append(self._msg("go_back_hint"))
                lines.append(self._reply_with_prompt(option_count))
                return "\n".join(lines)
            self.state = "INIT"
            return self._msg("no_clinic_available_restart")
        self.state = "INIT"
        return self._msg("no_clinic_available_restart")

    def _auto_select_single_clinic_after_phone(self) -> Optional[str]:
        self._ensure_actor_defaults()
        if not self.scheduling_repository or not self.doctor_id:
            return None
        self.clinic_options_cache = self._db_clinic_options()
        if len(self.clinic_options_cache) != 1:
            return None

        selected = self.clinic_options_cache[0]
        self.context.clinic_id = selected["id"]
        self.context.clinic_name = selected["name"]
        self.context.clinic_address = selected["address"]
        self.date_options_cache = []
        self.time_options_cache = []
        self.time_hour_options_cache = []
        self.time_slot_options_cache = []
        self.time_window_labels_cache = []
        self.selected_time_hour = None
        self.selected_time_period = None

        date_options = self._date_options()
        if not date_options:
            self.state = "ASK_CLINIC"
            return self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic") + "\n" + self._clinic_prompt()
        self.state = "ASK_DATE"
        return self._date_options_prompt(date_options)

    def _reply_with_prompt(self, option_count: int) -> str:
        if option_count <= 0:
            return self._msg("reply_with_numbers", numbers="0")
        numbers = ", ".join(str(i) for i in range(1, option_count + 1))
        return self._msg("reply_with_numbers", numbers=f"{numbers}, 0")

    def _date_options_prompt(self, date_options: list[str]) -> str:
        if not date_options:
            return self._msg("no_date_available", clinic_name=self.context.clinic_name or "this clinic")
        today_iso = date.today().isoformat()
        tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()
        def label(d: str) -> str:
            if d == today_iso:
                return f"Today ({d})"
            if d == tomorrow_iso:
                return f"Tomorrow ({d})"
            return d
        lines = ["Please choose appointment date:"]
        for idx, d in enumerate(date_options, start=1):
            lines.append(f"{idx}. {label(d)}")
        lines.append(self._msg("go_back_hint"))
        numbers = ", ".join(str(i) for i in range(1, len(date_options) + 1))
        lines.append(self._msg("reply_with_numbers", numbers=f"{numbers}, 0"))
        return "\n".join(lines)

    def _clinic_availability_line(self, clinic: dict) -> str:
        today_slots = int(clinic.get("today_slots") or 0)
        clinic_id = int(clinic["id"])

        if not self.scheduling_repository or not self.doctor_id:
            return "No upcoming slots"

        try:
            if today_slots > 0:
                today = date.today().isoformat()
                today_times = self.scheduling_repository.list_available_times(
                    doctor_id=self.doctor_id,
                    clinic_id=clinic_id,
                    slot_date=today,
                    admin_id=self.admin_id,
                    limit=50,
                )
                if today_times:
                    return (
                        "Timing: "
                        f"{self._format_time_for_display(today_times[0])}-{self._format_time_for_display(today_times[-1])}"
                    )
                return "No upcoming slots"

            next_dates = self.scheduling_repository.list_available_dates(
                doctor_id=self.doctor_id,
                clinic_id=clinic_id,
                admin_id=self.admin_id,
                limit=1,
            )
            if not next_dates:
                return "No upcoming slots"

            next_date = next_dates[0]
            next_times = self.scheduling_repository.list_available_times(
                doctor_id=self.doctor_id,
                clinic_id=clinic_id,
                slot_date=next_date,
                admin_id=self.admin_id,
                limit=50,
            )
            if next_times:
                return (
                    f"Next available: {next_date} | "
                    f"Timing: {self._format_time_for_display(next_times[0])}-{self._format_time_for_display(next_times[-1])}"
                )
            return f"Next available: {next_date}"
        except Exception:
            return "No upcoming slots"

    def _display_context(self) -> dict:
        data = dict(self.context.__dict__)
        if data.get("appointment_time"):
            data["appointment_time"] = self._format_time_for_display(str(data["appointment_time"]))
        return data

    @staticmethod
    def _format_time_for_display(raw_time: str) -> str:
        text = (raw_time or "").strip()
        if not text:
            return text
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return datetime.strptime(text, fmt).strftime("%I:%M %p")
            except ValueError:
                continue
        return text

    def _availability_reply(self, slot_date: str) -> str:
        self._ensure_actor_defaults()
        if not self.scheduling_repository or not self.doctor_id:
            if self.context.availability_doctor:
                return self._msg(
                    "availability_noted",
                    availability_doctor=self.context.availability_doctor,
                    availability_date=slot_date,
                )
            return (
                f"Noted. You want doctor availability on {slot_date}.\n"
                "Please share doctor name if you want doctor-specific availability."
            )

        try:
            clinics = self.scheduling_repository.list_clinics_for_doctor(
                doctor_id=self.doctor_id,
                admin_id=self.admin_id,
                limit=10,
            )
            if not clinics:
                return self._msg("no_clinic_available")

            available_lines: list[str] = []
            for clinic in clinics:
                times = self.scheduling_repository.list_available_times(
                    doctor_id=self.doctor_id,
                    clinic_id=clinic.clinic_id,
                    slot_date=slot_date,
                    admin_id=self.admin_id,
                    limit=50,
                )
                if times:
                    available_lines.append(
                        f"- {clinic.clinic_name}: {len(times)} slots ({self._format_time_for_display(times[0])}-{self._format_time_for_display(times[-1])})"
                    )

            if available_lines:
                return (
                    f"Doctor availability on {slot_date}:\n"
                    + "\n".join(available_lines)
                    + "\nReply with 'book appointment' to continue booking."
                )

            next_lines: list[str] = []
            for clinic in clinics:
                next_dates = self.scheduling_repository.list_available_dates(
                    doctor_id=self.doctor_id,
                    clinic_id=clinic.clinic_id,
                    admin_id=self.admin_id,
                    limit=1,
                )
                if next_dates:
                    next_lines.append(f"- {clinic.clinic_name}: {next_dates[0]}")

            if next_lines:
                return (
                    f"No slots available on {slot_date}.\n"
                    "Next available dates:\n"
                    + "\n".join(next_lines)
                )
            return f"No slots available on {slot_date}."
        except Exception:
            if self.context.availability_doctor:
                return self._msg(
                    "availability_noted",
                    availability_doctor=self.context.availability_doctor,
                    availability_date=slot_date,
                )
            return (
                f"Noted. You want doctor availability on {slot_date}.\n"
                "Please share doctor name if you want doctor-specific availability."
            )
