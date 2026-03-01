import json
from datetime import timedelta
from typing import Optional

from src.db_store import BookingRepository
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.conversation_repository import ConversationRepository
from src.repositories.scheduling_repository import SchedulingRepository

class SessionManager:
    def __init__(
        self,
        llm_client: LLMClient,
        mixed_response_language: str = "en",
        enable_llm_polish: bool = True,
        booking_repository: Optional[BookingRepository] = None,
        scheduling_repository: Optional[SchedulingRepository] = None,
        conversation_repository: Optional[ConversationRepository] = None,
        redis_client: Optional[object] = None,
        redis_key_prefix: str = "msgbot",
        bot_whatsapp_number: Optional[str] = None,
        ttl_minutes: int = 120,
    ) -> None:
        self.llm_client = llm_client
        self.mixed_response_language = mixed_response_language
        self.enable_llm_polish = enable_llm_polish
        self.booking_repository = booking_repository
        self.scheduling_repository = scheduling_repository
        self.conversation_repository = conversation_repository
        self.redis_client = redis_client
        self.redis_key_prefix = (redis_key_prefix or "msgbot").strip() or "msgbot"
        self.bot_whatsapp_number = bot_whatsapp_number
        self.ttl = timedelta(minutes=ttl_minutes)
        self._redis_ttl_seconds = max(60, int(self.ttl.total_seconds()))

    def get_or_create(self, user_id: str) -> AppointmentFSM:
        return self._load_or_create_fsm(user_id=user_id)

    def save(self, user_id: str, fsm: Optional[AppointmentFSM] = None) -> None:
        current_fsm = fsm or self._load_or_create_fsm(user_id=user_id)
        self._save_redis_snapshot(user_id=user_id, fsm=current_fsm)
        if not self.conversation_repository:
            return
        self.conversation_repository.save_session(
            user_id=user_id,
            state=current_fsm.state,
            context=current_fsm.context.__dict__,
            response_language=current_fsm.response_language,
            language_locked=current_fsm.language_locked,
            language_turn_count=current_fsm.language_turn_count,
            init_unclear_count=current_fsm.init_unclear_count,
            in_edit_flow=current_fsm.in_edit_flow,
            doctor_id=current_fsm.doctor_id,
            admin_id=current_fsm.admin_id,
            fsm_extra_json=json.dumps(self._fsm_extra_dict(current_fsm), ensure_ascii=False),
        )

    def _load_or_create_fsm(self, user_id: str) -> AppointmentFSM:
        fsm = AppointmentFSM(
            llm_client=self.llm_client,
            mixed_response_language=self.mixed_response_language,
            enable_llm_polish=self.enable_llm_polish,
            booking_repository=self.booking_repository,
            scheduling_repository=self.scheduling_repository,
            chat_phone_number=user_id,
            bot_whatsapp_number=self.bot_whatsapp_number,
        )
        redis_snapshot = self._load_redis_snapshot(user_id=user_id)
        if redis_snapshot:
            if self._apply_snapshot_to_fsm(fsm=fsm, snapshot=redis_snapshot):
                return fsm
        if not self.conversation_repository:
            return fsm
        snapshot = self.conversation_repository.load_session(user_id=user_id, ttl_minutes=int(self.ttl.total_seconds() // 60))
        if not snapshot:
            return fsm
        try:
            payload = {
                "state": snapshot.state or "INIT",
                "context": json.loads(snapshot.context_json or "{}"),
                "response_language": snapshot.response_language or "en",
                "language_locked": bool(snapshot.language_locked),
                "language_turn_count": int(snapshot.language_turn_count or 0),
                "init_unclear_count": int(snapshot.init_unclear_count or 0),
                "in_edit_flow": bool(snapshot.in_edit_flow),
                "doctor_id": snapshot.doctor_id,
                "admin_id": snapshot.admin_id,
            }
            # Unpack fsm_extra_json persisted by save_session so that booking_for_self,
            # time selection caches, etc. survive the DB round-trip.
            if snapshot.fsm_extra_json:
                try:
                    extra = json.loads(snapshot.fsm_extra_json)
                    payload.update(extra)
                except Exception:
                    pass
            self._apply_snapshot_to_fsm(fsm=fsm, snapshot=payload)
        except Exception:
            return fsm
        return fsm

    @staticmethod
    def _fsm_extra_dict(fsm: AppointmentFSM) -> dict:
        """Fields that live on the FSM dataclass (not in AppointmentContext) that must
        survive session reconstruction.  Stored as fsm_extra_json in DB and inline in
        the Redis snapshot."""
        return {
            "known_patient_name": fsm.known_patient_name,
            "booking_for_self": fsm.booking_for_self,
            "selected_time_period": fsm.selected_time_period,
            "time_slot_options_cache": list(fsm.time_slot_options_cache or []),
            "time_window_labels_cache": list(fsm.time_window_labels_cache or []),
            # Reschedule-flow fields — must survive every turn so ASK_TIME period
            # selection (turn N) → slot selection (turn N+1) still knows it's a
            # reschedule and routes to CONFIRM_RESCHEDULE, not CONFIRM.
            "in_reschedule_flow": bool(fsm.in_reschedule_flow),
            "pending_existing_action": fsm.pending_existing_action,
            "existing_appointment_id": fsm.existing_appointment_id,
            "existing_booking_clinic_id": fsm.existing_booking_clinic_id,
            "existing_booking_clinic_name": fsm.existing_booking_clinic_name,
            "existing_booking_doctor_id": fsm.existing_booking_doctor_id,
            "existing_booking_old_date": fsm.existing_booking_old_date,
            "existing_booking_old_time": fsm.existing_booking_old_time,
            # Booking-pick list — if session reconstructs at ASK_EXISTING_BOOKING_PICK
            # and cache is empty, the handler falls back to ASK_EXISTING_BOOKING_ACTION
            # forcing the patient to re-navigate.  Persisting avoids that UX regression.
            "active_booking_options_cache": list(fsm.active_booking_options_cache or []),
        }

    def _redis_key(self, user_id: str) -> str:
        return f"{self.redis_key_prefix}:sess:{(user_id or '').strip()}"

    def _load_redis_snapshot(self, user_id: str) -> Optional[dict]:
        if not self.redis_client:
            return None
        key = self._redis_key(user_id)
        if not key.strip():
            return None
        try:
            raw = self.redis_client.get(key)
            if not raw:
                return None
            return json.loads(str(raw))
        except Exception:
            return None

    def _save_redis_snapshot(self, *, user_id: str, fsm: AppointmentFSM) -> None:
        if not self.redis_client:
            return
        key = self._redis_key(user_id)
        if not key.strip():
            return
        payload = {
            "state": fsm.state,
            "context": fsm.context.__dict__,
            "response_language": fsm.response_language,
            "language_locked": bool(fsm.language_locked),
            "language_turn_count": int(fsm.language_turn_count or 0),
            "init_unclear_count": int(fsm.init_unclear_count or 0),
            "in_edit_flow": bool(fsm.in_edit_flow),
            "doctor_id": fsm.doctor_id,
            "admin_id": fsm.admin_id,
        }
        payload.update(self._fsm_extra_dict(fsm))
        try:
            self.redis_client.set(self._redis_key(user_id), json.dumps(payload, ensure_ascii=False), ex=self._redis_ttl_seconds)
        except Exception:
            return

    def _apply_snapshot_to_fsm(self, *, fsm: AppointmentFSM, snapshot: dict) -> bool:
        try:
            context_payload = snapshot.get("context") or {}
            if isinstance(context_payload, str):
                context_payload = json.loads(context_payload or "{}")
            for key, value in context_payload.items():
                if hasattr(fsm.context, key):
                    setattr(fsm.context, key, value)
            fsm.state = str(snapshot.get("state") or "INIT")
            fsm.response_language = str(snapshot.get("response_language") or "en")
            fsm.language_locked = bool(snapshot.get("language_locked"))
            fsm.language_turn_count = int(snapshot.get("language_turn_count") or 0)
            fsm.init_unclear_count = int(snapshot.get("init_unclear_count") or 0)
            fsm.in_edit_flow = bool(snapshot.get("in_edit_flow"))
            doctor_id = snapshot.get("doctor_id")
            admin_id = snapshot.get("admin_id")
            fsm.doctor_id = int(doctor_id) if doctor_id is not None else None
            fsm.admin_id = int(admin_id) if admin_id is not None else None
            known_patient_name = snapshot.get("known_patient_name")
            fsm.known_patient_name = str(known_patient_name) if known_patient_name else None
            # Restore FSM-level fields that control flow and slot selection
            bfs = snapshot.get("booking_for_self")
            fsm.booking_for_self = bool(bfs) if bfs is not None else None
            fsm.selected_time_period = snapshot.get("selected_time_period") or None
            raw_slots = snapshot.get("time_slot_options_cache")
            fsm.time_slot_options_cache = list(raw_slots) if isinstance(raw_slots, list) else []
            raw_labels = snapshot.get("time_window_labels_cache")
            fsm.time_window_labels_cache = list(raw_labels) if isinstance(raw_labels, list) else []
            # Reschedule-flow fields
            fsm.in_reschedule_flow = bool(snapshot.get("in_reschedule_flow"))
            fsm.pending_existing_action = snapshot.get("pending_existing_action") or None
            existing_appt_id = snapshot.get("existing_appointment_id")
            fsm.existing_appointment_id = int(existing_appt_id) if existing_appt_id is not None else None
            fsm.existing_booking_clinic_id = snapshot.get("existing_booking_clinic_id") or None
            fsm.existing_booking_clinic_name = snapshot.get("existing_booking_clinic_name") or None
            existing_dr_id = snapshot.get("existing_booking_doctor_id")
            fsm.existing_booking_doctor_id = int(existing_dr_id) if existing_dr_id is not None else None
            fsm.existing_booking_old_date = snapshot.get("existing_booking_old_date") or None
            fsm.existing_booking_old_time = snapshot.get("existing_booking_old_time") or None
            raw_booking_opts = snapshot.get("active_booking_options_cache")
            fsm.active_booking_options_cache = list(raw_booking_opts) if isinstance(raw_booking_opts, list) else []
            return True
        except Exception:
            return False
