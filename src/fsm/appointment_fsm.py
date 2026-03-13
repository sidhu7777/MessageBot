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
from src.fsm.handlers.booking import (
    handle_ask_booking_for_state,
    handle_ask_clinic_state,
    handle_ask_name_state,
    handle_ask_phone_state,
    handle_ask_time_state,
)
from src.fsm.handlers.existing import (
    handle_confirm_reschedule_state,
    handle_existing_booking_action_state,
    handle_existing_booking_pick_state,
    handle_max_active_bookings_action_state,
)
from src.fsm.handlers.init_availability import (
    handle_ask_date_state,
    handle_availability_date_state,
    handle_availability_details_state,
    handle_cancelled_state,
    handle_change_field_state,
    handle_completed_state,
    handle_confirm_state,
    handle_init_state,
)
from src.nlu.extractors import (
    capture_prefill_entities,
    extract_date,
    extract_doctor_name,
    extract_name,
    extract_phone,
    extract_time,
    is_booking_intent,
    is_end_intent,
    is_greeting_intent,
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
    phone_number: Optional[str] = None
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
    availability_date_options_cache: list[str] = field(default_factory=list)
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
    known_patient_phone: Optional[str] = None
    pending_init_intent: Optional[str] = None
    language_selected_by_user: bool = False

    def handle(self, user_text: str) -> str:
        text = (user_text or "").strip()
        lower = text.lower()
        text, lower = self._normalize_option_input_for_state(text, lower)

        if self.context.abuse_blocked:
            return ""

        if not text:
            return self._respond(self._msg("empty_input"))

        if self._is_abusive_message(text, lower, allow_llm=False):
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
            return handle_init_state(self, text, lower)
        if self.state == "ASK_LANGUAGE":
            return handle_init_state(self, text, lower)
        if self.state == "CANCELLED":
            return handle_cancelled_state(self, lower)
        if self.state == "ASK_BOOKING_FOR":
            return handle_ask_booking_for_state(self, lower)
        if self.state == "ASK_NAME":
            return handle_ask_name_state(self, text, lower)
        if self.state == "ASK_EXISTING_BOOKING_ACTION":
            return handle_existing_booking_action_state(self, lower)
        if self.state == "ASK_MAX_ACTIVE_BOOKINGS_ACTION":
            return handle_max_active_bookings_action_state(self, lower)
        if self.state == "ASK_EXISTING_BOOKING_PICK":
            return handle_existing_booking_pick_state(self, lower)
        if self.state == "ASK_AVAILABILITY_DATE":
            return handle_availability_date_state(self, lower)
        if self.state == "ASK_AVAILABILITY_DETAILS":
            return handle_availability_details_state(self, text, lower)
        if self.state == "ASK_PHONE":
            return handle_ask_phone_state(self, text, lower)
        if self.state == "ASK_CLINIC":
            return handle_ask_clinic_state(self, text, lower)
        if self.state == "ASK_DATE":
            return handle_ask_date_state(self, lower)
        if self.state == "ASK_TIME":
            return handle_ask_time_state(self, text, lower)
        if self.state == "CONFIRM_RESCHEDULE":
            return handle_confirm_reschedule_state(self, lower)
        if self.state == "CONFIRM":
            return handle_confirm_state(self, text, lower)
        if self.state == "ASK_CHANGE_FIELD":
            return handle_change_field_state(self, text, lower)
        if self.state == "COMPLETED":
            return handle_completed_state(self, lower)

        self._reset_all(cancelled=False)
        self.state = "ASK_NAME"
        return self._respond(self._msg("ask_name"))

    def _normalize_option_input_for_state(self, text: str, lower: str) -> tuple[str, str]:
        option_states = {
            "ASK_LANGUAGE",
            "ASK_BOOKING_FOR",
            "ASK_EXISTING_BOOKING_ACTION",
            "ASK_MAX_ACTIVE_BOOKINGS_ACTION",
            "ASK_EXISTING_BOOKING_PICK",
            "ASK_APPOINTMENT_MODE",
            "ASK_AVAILABILITY_DATE",
            "ASK_CLINIC",
            "ASK_DATE",
            "ASK_TIME",
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
        self.known_patient_phone = None
        self.pending_init_intent = None
        self.language_selected_by_user = False
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
            return text + "\n\n" + self._msg("go_back_hint")
        return text + "\n\n" + self._msg("go_back_hint")

    @staticmethod
    def _is_go_back(lower_text: str) -> bool:
        val = (lower_text or "").strip().lower()
        return val in {"go back", "back", "previous", "prev", "menu", "0", "c"}

    def _handle_go_back(self) -> Optional[str]:
        if self.state == "ASK_BOOKING_FOR":
            self.state = "INIT"
            return self._msg("clarify_intent")
        if self.state == "ASK_NAME":
            self.state = "ASK_BOOKING_FOR"
            return self._with_back(self._msg("ask_booking_for"), option_count=2)
        if self.state == "ASK_LANGUAGE":
            return self._language_selection_prompt()
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
        if self.state == "ASK_AVAILABILITY_DATE":
            self.state = "INIT"
            self.availability_date_options_cache = []
            return self._msg("clarify_intent")
        if self.state == "ASK_AVAILABILITY_DETAILS":
            self.state = "ASK_AVAILABILITY_DATE"
            self.context.availability_date = None
            if not self.availability_date_options_cache:
                self.availability_date_options_cache = self._availability_date_options()
            return self._availability_date_options_prompt(self.availability_date_options_cache)
        if self.state == "ASK_CHANGE_FIELD":
            self.state = "CONFIRM"
            return self._msg("confirm_summary", **self._display_context())
        if self.state == "CONFIRM":
            self.state = "ASK_TIME"
            # time_options_cache / time_hour_options_cache may be empty if the session
            # was restored from Redis without those fields (e.g. older snapshot) or if
            # they were never populated.  Re-load from DB so _initial_time_prompt() has
            # data to work with; only do the DB call when the cache is actually empty.
            if not self.time_options_cache:
                self._load_time_options(limit=60)
            return self._initial_time_prompt()
        return None

    def _welcome_greeting(self) -> str:
        # known_patient_name is already hydrated by _hydrate_known_patient_name()
        # which is called unconditionally at the top of handle() every turn.
        # We only need to fetch the doctor display name here.
        doctor_name = self._doctor_display_name()
        if self.known_patient_name:
            return self._msg(
                "welcome_known_patient",
                doctor_name=doctor_name,
                patient_name=self.known_patient_name,
            )
        return self._msg("welcome_new_patient", doctor_name=doctor_name)

    def _welcome_booking_start(self) -> str:
        doctor_name = self._doctor_display_name()
        if self.known_patient_name:
            return self._msg(
                "welcome_known_patient_booking",
                doctor_name=doctor_name,
                patient_name=self.known_patient_name,
            )
        return self._msg("welcome_new_patient_booking", doctor_name=doctor_name)

    def _language_selection_prompt(self) -> str:
        doctor_name = self._doctor_display_name()
        if self.known_patient_name:
            return self._msg(
                "language_selection_known_patient",
                doctor_name=doctor_name,
                patient_name=self.known_patient_name,
            )
        return self._msg("language_selection_new_patient", doctor_name=doctor_name)

    def _doctor_display_name(self) -> str:
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
        return doctor_name

    def _is_init_safe_input(self, lower: str) -> bool:
        """Return True for INIT-state inputs that are obviously safe and can skip LLM abuse detection.
        Only exact single-digit menu options and pure short greetings qualify.
        Multi-word free-text like 'hello fucker' is NOT safe and falls through to LLM."""
        stripped = lower.strip()
        # Direct menu digit options — patient pressed 1, 2, or 0
        if stripped in {"0", "1", "2"}:
            return True
        # Pure greeting — one or two words only ("hi", "hello", "hello there")
        if is_greeting_intent(stripped) and len(stripped.split()) <= 2:
            return True
        return False

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
                f"{idx}. {row.get('clinic_name') or '-'}, Date: {row.get('slot_date') or '-'}, "
                f"Time: {self._format_time_for_display(str(row.get('slot_time') or '-'))}, Booking Number: {display_number}"
            )
        lines.append("")
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
        if self.response_language == "hi":
            labels = {
                "morning": "सुबह",
                "afternoon": "दोपहर",
                "evening": "शाम",
            }
            header = "कृपया पसंदीदा समय अवधि चुनें:"
        elif self.response_language == "hinglish":
            labels = {
                "morning": "Morning",
                "afternoon": "Afternoon",
                "evening": "Evening",
            }
            header = "Please preferred time period choose kariye:"
        else:
            labels = {
                "morning": "Morning",
                "afternoon": "Afternoon",
                "evening": "Evening",
            }
            header = "Please choose preferred time period:"
        lines = [header]
        for idx, period in enumerate(periods, start=1):
            lines.append(f"{idx}. {labels.get(period, period.title())}")
        lines.append("")
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
        lines.append("")
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
                # Also retrieve phone number for Telegram patients
                if patient_name:
                    patient_phone = self.booking_repository.find_patient_phone_by_chat_user_id(
                        chat_user_id=raw_chat,
                        admin_id=self.admin_id,
                        doctor_id=self.doctor_id,
                    )
                    self.known_patient_phone = patient_phone
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
                    lines.append(f"{clinic['ordinal']}. {clinic['name']}, Address: {clinic['address']}")
                option_count = len(self.clinic_options_cache[:10])
                lines.append("")
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
                return self._msg("date_label_today", date=d)
            if d == tomorrow_iso:
                return self._msg("date_label_tomorrow", date=d)
            return d
        lines = [self._msg("date_options_header")]
        for idx, d in enumerate(date_options, start=1):
            lines.append(f"{idx}. {label(d)}")
        lines.append("")
        lines.append(self._msg("go_back_hint"))
        numbers = ", ".join(str(i) for i in range(1, len(date_options) + 1))
        lines.append(self._msg("reply_with_numbers", numbers=f"{numbers}, 0"))
        return "\n".join(lines)

    def _availability_date_options(self) -> list[str]:
        """Return upcoming dates based on doctor's accept_days (no clinic_id needed)."""
        self._ensure_actor_defaults()
        if self.scheduling_repository and self.doctor_id:
            try:
                accept_days = self.scheduling_repository.doctor_accept_days(
                    doctor_id=self.doctor_id,
                    admin_id=self.admin_id,
                )
                limit = max(1, min(14, int(accept_days) + 1))
                today = date.today()
                return [(today + timedelta(days=i)).isoformat() for i in range(limit)]
            except Exception:
                pass
        # Fallback: today + tomorrow
        today = date.today()
        return [today.isoformat(), (today + timedelta(days=1)).isoformat()]

    def _availability_date_options_prompt(self, date_options: list[str]) -> str:
        """Numbered menu for the ASK_AVAILABILITY_DATE state."""
        today_iso = date.today().isoformat()
        tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()

        def label(d: str) -> str:
            if d == today_iso:
                return self._msg("date_label_today", date=d)
            if d == tomorrow_iso:
                return self._msg("date_label_tomorrow", date=d)
            return d

        lines = [self._msg("availability_date_options_header")]
        for idx, d in enumerate(date_options, start=1):
            lines.append(f"{idx}. {label(d)}")
        lines.append("")
        lines.append(self._msg("go_back_hint"))
        return "\n".join(lines)

    def _display_context(self) -> dict:
        data = dict(self.context.__dict__)
        # Template compatibility for legacy placeholders that may still exist
        # in message variants.
        data.setdefault("patient_type", "")
        data.setdefault("age", "")
        data.setdefault("gender", "")
        data.setdefault("reason", "")
        data.setdefault("symptoms", "")
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
            return self._msg("availability_generic_noted", availability_date=slot_date)

        try:
            # Redis-primary path: fetch one snapshot and derive all clinic/date/time output.
            # Repository keeps DB fallback if cache is missing/invalid.
            snapshot = self.scheduling_repository.get_availability_snapshot(
                doctor_id=self.doctor_id,
                admin_id=self.admin_id,
            )
            clinics_payload = list((snapshot or {}).get("clinics") or [])
            if not clinics_payload:
                return self._msg("no_clinic_available")

            dates_by_clinic = dict((snapshot or {}).get("dates_by_clinic") or {})
            times_by_clinic_date = dict((snapshot or {}).get("times_by_clinic_date") or {})

            available_lines: list[str] = []
            next_lines: list[str] = []
            for row in clinics_payload[:10]:
                clinic_id = int(row.get("clinic_id") or 0)
                clinic_name = str(row.get("clinic_name") or "-")
                clinic_key = str(clinic_id)
                clinic_dates = list(dates_by_clinic.get(clinic_key) or [])
                if slot_date in clinic_dates:
                    times = list(times_by_clinic_date.get(f"{clinic_key}|{slot_date}") or [])
                    if times:
                        count = len(times)
                        if count == 1:
                            slot_label = f"1 slot at {self._format_time_for_display(times[0])}"
                        else:
                            slot_label = (
                                f"{count} slots ("
                                f"{self._format_time_for_display(times[0])} - {self._format_time_for_display(times[-1])})"
                            )
                        available_lines.append(f"- {clinic_name}: {slot_label}")
                    else:
                        available_lines.append(f"- {clinic_name}: Available")
                elif clinic_dates:
                    next_lines.append(f"- {clinic_name}: {clinic_dates[0]}")

            if available_lines:
                return (
                    self._msg("availability_result_available_header", availability_date=slot_date)
                    + "\n"
                    + "\n".join(available_lines)
                    + self._msg("availability_result_actions")
                )

            if next_lines:
                return (
                    self._msg("availability_result_next_header", availability_date=slot_date)
                    + "\n"
                    + "\n".join(next_lines)
                    + self._msg("availability_result_actions_back")
                )
            return self._msg("availability_result_none", availability_date=slot_date)
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
