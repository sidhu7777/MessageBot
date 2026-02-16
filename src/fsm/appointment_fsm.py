import logging
from dataclasses import dataclass, field
from typing import Optional

from src.db_store import BookingRepository, SchedulingRepository
from src.llm.client import LLMClient
from src.llm.tasks import (
    llm_change_target,
    llm_detect_confirm_intent,
    llm_extract,
)
from src.messages.templates import get_message
from src.nlu.extractors import (
    capture_prefill_entities,
    extract_age,
    extract_date,
    extract_doctor_name,
    extract_gender,
    extract_name,
    extract_patient_type,
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

@dataclass
class AppointmentContext:
    patient_name: Optional[str] = None
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
    existing_appointment_id: Optional[int] = None
    existing_booking_clinic_id: Optional[str] = None
    existing_booking_clinic_name: Optional[str] = None
    existing_booking_doctor_id: Optional[int] = None
    existing_booking_old_date: Optional[str] = None
    existing_booking_old_time: Optional[str] = None
    in_reschedule_flow: bool = False

    def handle(self, user_text: str) -> str:
        text = (user_text or "").strip()
        lower = text.lower()

        if not text:
            return self._respond(self._msg("empty_input"))

        if is_end_intent(lower):
            self._reset_all(cancelled=True)
            return self._respond(self._msg("ended"))

        self._update_response_language(lower)
        capture_prefill_entities(self.context, text)

        if is_restart_intent(lower):
            self._reset_all(cancelled=False)
            self.state = "ASK_NAME"
            return self._respond(self._msg("restart") + "\n" + self._msg("ask_name"))

        if self.state == "INIT":
            if self.init_unclear_count >= 3:
                if is_yes(lower) or is_booking_intent(lower):
                    self.init_unclear_count = 0
                    self.state = "ASK_NAME"
                    return self._respond(
                        self._msg("intent_ack") + "\n" + self._msg("ask_name"),
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
                self.state = "ASK_NAME"
                return self._respond(
                    self._msg("intent_ack") + "\n" + self._msg("ask_name"),
                    allow_polish=False,
                )
            if routed == "CHECK_AVAILABILITY":
                self.init_unclear_count = 0
                self.state = "ASK_AVAILABILITY_DETAILS"
                return self._respond(self._msg("availability_intro"), allow_polish=False)
            if routed == "GREETING":
                # Greeting is treated as a soft-init turn so that next unclear turn can move to disambiguation.
                self.init_unclear_count = 1
                return self._respond(self._msg("greeting"), allow_polish=False)
            if routed == "GENERAL_QUERY":
                self.init_unclear_count += 1
                if self.init_unclear_count == 2:
                    return self._respond(self._msg("clarify_intent"), allow_polish=False)
                if self.init_unclear_count >= 3:
                    return self._respond(self._msg("final_booking_check"), allow_polish=False)
                return self._respond(self._msg("general_help"), allow_polish=False)
            self.init_unclear_count += 1
            if self.init_unclear_count == 2:
                return self._respond(self._msg("clarify_intent"), allow_polish=False)
            if self.init_unclear_count >= 3:
                return self._respond(self._msg("final_booking_check"), allow_polish=False)
            return self._respond(self._msg("no_intent"), allow_polish=False)

        if self.state == "CANCELLED":
            if is_booking_intent(lower) or is_restart_intent(lower):
                self._reset_all(cancelled=False)
                self.state = "ASK_NAME"
                return self._respond(self._msg("ask_name"))
            return self._respond(self._msg("cancelled_hint"))

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
            if not self.in_edit_flow and self.booking_repository:
                existing = self.booking_repository.find_active_appointment_by_patient_name(
                    patient_name=name,
                    admin_id=self.admin_id,
                )
                if existing:
                    self.existing_appointment_id = int(existing["appointment_id"])
                    self.existing_booking_clinic_id = str(existing.get("clinic_id") or "")
                    self.existing_booking_clinic_name = str(existing.get("clinic_name") or "")
                    self.existing_booking_doctor_id = int(existing.get("doctor_id") or 0) or None
                    self.existing_booking_old_date = str(existing.get("slot_date") or "")
                    self.existing_booking_old_time = str(existing.get("slot_time") or "")
                    self.state = "ASK_EXISTING_BOOKING_ACTION"
                    return self._respond(
                        self._msg(
                            "existing_booking_found",
                            appointment_id=existing["appointment_id"],
                            appointment_date=existing.get("slot_date") or "-",
                            appointment_time=existing.get("slot_time") or "-",
                            clinic_name=existing.get("clinic_name") or "-",
                        )
                    )
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_NAME") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_PATIENT_TYPE"
            return self._respond(self._msg("name_ack", name=name) + "\n" + self._msg("ask_patient_type"))

        if self.state == "ASK_EXISTING_BOOKING_ACTION":
            normalized = lower.strip()
            if normalized in {"1", "keep", "no", "do not cancel"}:
                self.state = "COMPLETED"
                return self._respond(self._msg("existing_booking_keep"))
            if normalized in {"2", "cancel", "cancel only"}:
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
                self.in_reschedule_flow = True
                self.context.clinic_id = None
                self.context.clinic_name = None
                self.context.clinic_address = None
                self.context.appointment_date = None
                self.context.appointment_time = None
                self.clinic_options_cache = []
                self.date_options_cache = []
                self.time_options_cache = []
                self.state = "ASK_CLINIC"
                return self._respond(
                    self._msg("existing_booking_reschedule_start", clinic_name=self.existing_booking_clinic_name or "-")
                    + "\n"
                    + self._clinic_prompt()
                )
            return self._respond(self._msg("existing_booking_choice_invalid"))

        if self.state == "ASK_AVAILABILITY_DETAILS":
            if is_booking_intent(lower) or is_restart_intent(lower):
                self.context = AppointmentContext()
                self.state = "ASK_NAME"
                return self._respond(self._msg("intent_ack") + "\n" + self._msg("ask_name"))

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

            if self.context.availability_doctor and self.context.availability_date:
                return self._respond(
                    self._msg(
                        "availability_noted",
                        availability_doctor=self.context.availability_doctor,
                        availability_date=self.context.availability_date,
                    )
                )
            if not self.context.availability_doctor and not self.context.availability_date:
                return self._respond(self._msg("availability_ask"))
            if not self.context.availability_doctor:
                return self._respond(self._msg("availability_ask_doctor"))
            return self._respond(self._msg("availability_ask_date"))

        if self.state == "ASK_PATIENT_TYPE":
            patient_type = extract_patient_type(lower)
            if not patient_type:
                patient_type = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="patient_type",
                    text=text,
                )
            if not patient_type:
                return self._respond(self._msg("invalid_patient_type"))
            self.context.patient_type = patient_type
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_PATIENT_TYPE") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_AGE"
            return self._respond(self._msg("patient_type_ack", patient_type=patient_type) + "\n" + self._msg("ask_age"))

        if self.state == "ASK_AGE":
            age = extract_age(lower)
            if age is None:
                llm_age = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="age",
                    text=text,
                )
                age = int(llm_age) if llm_age and str(llm_age).isdigit() else None
            if age is None:
                return self._respond(self._msg("invalid_age"))
            self.context.age = age
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_AGE") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_GENDER"
            return self._respond(self._msg("age_ack", age=age) + "\n" + self._msg("ask_gender"))

        if self.state == "ASK_GENDER":
            gender = extract_gender(lower)
            if not gender:
                gender = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="gender",
                    text=text,
                )
            if not gender:
                return self._respond(self._msg("invalid_gender"))
            self.context.gender = gender
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_GENDER") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_PHONE"
            return self._respond(self._msg("gender_ack", gender=gender) + "\n" + self._msg("ask_phone"))

        if self.state == "ASK_PHONE":
            chat_phone = self._normalize_phone(self.chat_phone_number or "")
            normalized = lower.strip()
            same_number_markers = {
                "1",
                "same",
                "same number",
                "same as this number",
                "same as whatsapp number",
                "this number",
                "chat number",
                "whatsapp number",
                "use same",
            }
            if normalized in same_number_markers:
                if not chat_phone:
                    return self._respond(self._msg("invalid_phone_same_missing"))
                self.context.phone_number = chat_phone
                if self.in_edit_flow:
                    self.in_edit_flow = False
                    self.state = "CONFIRM"
                    return self._respond(self._msg("change_ack", step="ASK_PHONE") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
                self.state = "ASK_CLINIC"
                return self._respond(
                    self._msg("phone_ack", phone_number=self.context.phone_number) + "\n" + self._clinic_prompt()
                )

            phone = extract_phone(text)
            if not phone and normalized in {"2", "new", "different", "different number", "new number"}:
                return self._respond(self._msg("invalid_phone"))
            if not phone:
                llm_phone = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="phone",
                    text=text,
                )
                phone = self._normalize_phone(str(llm_phone)) if llm_phone else None
            if not phone:
                return self._respond(self._msg("invalid_phone"))
            self.context.phone_number = phone
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_PHONE") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_CLINIC"
            return self._respond(self._msg("phone_ack", phone_number=phone) + "\n" + self._clinic_prompt())

        if self.state == "ASK_CLINIC":
            selected = self._select_clinic(text, lower)
            if not selected:
                return self._respond(self._msg("invalid_clinic"))
            self.context.clinic_id = selected["id"]
            self.context.clinic_name = selected["name"]
            self.context.clinic_address = selected["address"]
            self.date_options_cache = []
            self.time_options_cache = []
            if self.in_edit_flow:
                # Clinic change affects slot pool, so re-collect date+time only.
                self.context.appointment_date = None
                self.context.appointment_time = None
            date_options = self._date_options()
            if not date_options:
                self.state = "ASK_CLINIC"
                return self._respond(self._msg("no_date_available") + "\n" + self._clinic_prompt())
            self.state = "ASK_DATE"
            d1, d2, d3 = date_options
            return self._respond(
                self._msg(
                    "clinic_ack",
                    clinic_name=self.context.clinic_name,
                    clinic_address=self.context.clinic_address,
                )
                + "\n"
                + self._msg("ask_date_options", date_1=d1, date_2=d2, date_3=d3)
            )

        if self.state == "ASK_DATE":
            normalized = lower.strip()
            date_options = self._date_options()
            if not date_options:
                self.state = "ASK_CLINIC"
                return self._respond(self._msg("no_date_available") + "\n" + self._clinic_prompt())
            d1, d2, d3 = date_options
            if normalized in {"1", "option 1", "date 1"}:
                parsed_date = d1
            elif normalized in {"2", "option 2", "date 2"}:
                parsed_date = d2
            elif normalized in {"3", "option 3", "date 3"}:
                parsed_date = d3
            elif normalized in {"4", "other", "other date"}:
                return self._respond(self._msg("ask_date_manual"))
            else:
                parsed_date = extract_date(text)
            if not parsed_date:
                parsed_date = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="date",
                    text=text,
                )
            if not parsed_date:
                return self._respond(
                    self._msg("invalid_date")
                    + "\n"
                    + self._msg("ask_date_options", date_1=d1, date_2=d2, date_3=d3)
                )
            self.context.appointment_date = parsed_date
            self.time_options_cache = []
            self.state = "ASK_TIME"
            slots = self._suggested_slots()
            if len(slots) < 3:
                self.state = "ASK_DATE"
                return self._respond(self._msg("no_time_available"))
            return self._respond(
                self._msg("date_ack", appointment_date=parsed_date)
                + "\n"
                + self._msg("ask_time_slots", slot_1=slots[0], slot_2=slots[1], slot_3=slots[2])
            )

        if self.state == "ASK_TIME":
            date_from_text = extract_date(text)
            if date_from_text and date_from_text != self.context.appointment_date:
                self.context.appointment_date = date_from_text
                slots = self._suggested_slots()
                if len(slots) < 3:
                    self.state = "ASK_DATE"
                    return self._respond(self._msg("no_time_available"))
                return self._respond(
                    self._msg("date_ack", appointment_date=date_from_text)
                    + "\n"
                    + self._msg("ask_time_slots", slot_1=slots[0], slot_2=slots[1], slot_3=slots[2])
                )

            slots = self._suggested_slots()
            if len(slots) < 3:
                self.state = "ASK_DATE"
                return self._respond(self._msg("no_time_available"))
            selected_time: Optional[str] = None
            normalized = lower.strip()
            if normalized in {"1", "slot 1", "option 1"}:
                selected_time = slots[0]
            elif normalized in {"2", "slot 2", "option 2"}:
                selected_time = slots[1]
            elif normalized in {"3", "slot 3", "option 3"}:
                selected_time = slots[2]

            parsed_time = selected_time if selected_time else extract_time(text)
            if not parsed_time:
                parsed_time = llm_extract(
                    llm_client=self.llm_client,
                    enable_llm_polish=self.enable_llm_polish,
                    field_name="time",
                    text=text,
                )
            if not parsed_time:
                return self._respond(self._msg("invalid_time"))
            if not self._is_available_time(parsed_time):
                return self._respond(
                    self._msg("time_not_available", requested_time=parsed_time)
                    + "\n"
                    + self._msg("ask_time_slots", slot_1=slots[0], slot_2=slots[1], slot_3=slots[2])
                )
            self.context.appointment_time = parsed_time
            if self.in_reschedule_flow:
                self.state = "CONFIRM_RESCHEDULE"
                return self._respond(
                    self._msg(
                        "confirm_reschedule_summary",
                        old_date=self.existing_booking_old_date or "-",
                        old_time=self.existing_booking_old_time or "-",
                        new_date=self.context.appointment_date or "-",
                        new_time=self.context.appointment_time or "-",
                        clinic_name=self.context.clinic_name or "-",
                    )
                )
            if self.in_edit_flow:
                self.in_edit_flow = False
                self.state = "CONFIRM"
                return self._respond(self._msg("change_ack", step="ASK_TIME") + "\n" + self._msg("confirm_summary", **self.context.__dict__))
            self.state = "ASK_REASON"
            return self._respond(self._msg("time_ack", appointment_time=parsed_time) + "\n" + self._msg("ask_reason"))

        if self.state == "CONFIRM_RESCHEDULE":
            if is_yes(lower):
                if not self.booking_repository or not self.existing_appointment_id:
                    self.in_reschedule_flow = False
                    self.state = "COMPLETED"
                    return self._respond(self._msg("existing_booking_cancel_failed"))
                result = self.booking_repository.reschedule_appointment_same_clinic(
                    appointment_id=self.existing_appointment_id,
                    new_date=self.context.appointment_date or "",
                    new_time=self.context.appointment_time or "",
                    admin_id=self.admin_id,
                )
                self.in_reschedule_flow = False
                self.state = "COMPLETED"
                if result.ok:
                    return self._respond(
                        self._msg(
                            "reschedule_confirmed",
                            appointment_id=result.appointment_id,
                            clinic_name=self.context.clinic_name or "-",
                            appointment_date=self.context.appointment_date or "-",
                            appointment_time=self.context.appointment_time or "-",
                        )
                    )
                return self._respond(self._msg("reschedule_failed"))
            if is_no(lower):
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
            if self.in_edit_flow:
                # Reason and symptoms are coupled; if reason changes, recollect symptoms.
                self.context.symptoms = None
                self.in_edit_flow = False
                self.state = "ASK_SYMPTOMS"
                return self._respond(self._msg("reason_ack") + "\n" + self._msg("ask_symptoms"))
            self.state = "ASK_SYMPTOMS"
            return self._respond(self._msg("reason_ack") + "\n" + self._msg("ask_symptoms"))

        if self.state == "ASK_SYMPTOMS":
            if len(text) < 3:
                return self._respond(self._msg("invalid_symptoms"))
            self.context.symptoms = text
            if self.in_edit_flow:
                self.in_edit_flow = False
            self.state = "CONFIRM"
            return self._respond(self._msg("symptoms_ack") + "\n" + self._msg("confirm_summary", **self.context.__dict__))

        if self.state == "CONFIRM":
            confirm_intent = self._detect_confirm_intent(text)
            if confirm_intent == "yes":
                self.state = "COMPLETED"
                confirmed = self._msg("confirmed", **self.context.__dict__)
                persist_note = self._persist_confirmed_appointment()
                if persist_note:
                    confirmed = confirmed + "\n" + persist_note
                return self._respond(confirmed)
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
                    return self._respond(self._msg("change_ack", step=reroute_state))
                self.state = "ASK_CHANGE_FIELD"
                return self._respond(self._msg("ask_change_field"))
            return self._respond(self._msg("confirm_prompt"))

        if self.state == "ASK_CHANGE_FIELD":
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
                return self._respond(self._msg("invalid_change_field"))
            self.in_edit_flow = True
            self.state = reroute_state
            return self._respond(self._msg("change_ack", step=reroute_state))

        if self.state == "COMPLETED":
            if is_booking_intent(lower) or is_restart_intent(lower):
                self._reset_all(cancelled=False)
                self.state = "ASK_NAME"
                return self._respond(self._msg("ask_name"))
            return self._respond(self._msg("completed_hint"))

        self._reset_all(cancelled=False)
        self.state = "ASK_NAME"
        return self._respond(self._msg("ask_name"))

    def _respond(self, base_text: str, allow_polish: bool = True) -> str:
        return base_text

    def _persist_confirmed_appointment(self) -> str:
        if not self.booking_repository:
            return ""
        try:
            result = self.booking_repository.save_confirmed_appointment(self.context, admin_id=self.admin_id)
        except Exception as exc:
            LOGGER.warning("DB persistence failed: %s", exc)
            return self._msg("db_save_failed")
        if result.ok:
            return self._msg("db_save_ok", appointment_id=result.appointment_id)
        return self._msg("db_save_failed")

    def _detect_confirm_intent(self, text: str) -> str:
        lower = text.lower()
        if is_yes(lower):
            return "yes"
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
        self.clinic_options_cache = []
        self.date_options_cache = []
        self.time_options_cache = []
        self.existing_appointment_id = None
        self._reset_existing_booking_flags()
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

    def _msg(self, key: str, **kwargs: object) -> str:
        return get_message(self.response_language, key, **kwargs)

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

    def _normalize_phone(self, value: str) -> Optional[str]:
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[-10:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[-10:]
        if len(digits) == 10:
            return digits
        return None

    def _date_options(self) -> Optional[tuple[str, str, str]]:
        self._ensure_actor_defaults()
        if self.scheduling_repository and self.doctor_id and self.context.clinic_id:
            if not self.date_options_cache:
                self.date_options_cache = self.scheduling_repository.list_available_dates(
                    doctor_id=self.doctor_id,
                    clinic_id=int(self.context.clinic_id),
                    admin_id=self.admin_id,
                    limit=3,
                )
            if len(self.date_options_cache) >= 3:
                return (
                    self.date_options_cache[0],
                    self.date_options_cache[1],
                    self.date_options_cache[2],
                )
            if len(self.date_options_cache) == 2:
                return (
                    self.date_options_cache[0],
                    self.date_options_cache[1],
                    self.date_options_cache[1],
                )
            if len(self.date_options_cache) == 1:
                return (
                    self.date_options_cache[0],
                    self.date_options_cache[0],
                    self.date_options_cache[0],
                )
            return None
        return None

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
            "2": "ASK_PATIENT_TYPE",
            "3": "ASK_AGE",
            "4": "ASK_GENDER",
            "5": "ASK_PHONE",
            "6": "ASK_CLINIC",
            "7": "ASK_DATE",
            "8": "ASK_TIME",
            "9": "ASK_REASON",
            "10": "ASK_SYMPTOMS",
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
                    self.doctor_id = self.scheduling_repository.default_doctor_id_by_phone(
                        phone_number=self.bot_whatsapp_number,
                        admin_id=self.admin_id,
                    )
                if self.doctor_id is None:
                    self.doctor_id = self.scheduling_repository.default_doctor_id(admin_id=self.admin_id)
            except Exception:
                self.doctor_id = None

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
                    lines.append(
                        f"{clinic['ordinal']}. {clinic['name']} | {clinic['address']} | Slots today: {clinic['today_slots']}"
                    )
                return "\n".join(lines)
            return self._msg("no_clinic_available")
        return self._msg("no_clinic_available")
