import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Dict, Optional

from src.db_store import BookingRepository
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.conversation_repository import ConversationRepository
from src.repositories.scheduling_repository import SchedulingRepository


@dataclass
class SessionEntry:
    fsm: AppointmentFSM
    last_seen_utc: datetime


class SessionManager:
    def __init__(
        self,
        llm_client: LLMClient,
        mixed_response_language: str = "en",
        enable_llm_polish: bool = True,
        booking_repository: Optional[BookingRepository] = None,
        scheduling_repository: Optional[SchedulingRepository] = None,
        conversation_repository: Optional[ConversationRepository] = None,
        bot_whatsapp_number: Optional[str] = None,
        ttl_minutes: int = 120,
    ) -> None:
        self.llm_client = llm_client
        self.mixed_response_language = mixed_response_language
        self.enable_llm_polish = enable_llm_polish
        self.booking_repository = booking_repository
        self.scheduling_repository = scheduling_repository
        self.conversation_repository = conversation_repository
        self.bot_whatsapp_number = bot_whatsapp_number
        self.ttl = timedelta(minutes=ttl_minutes)
        self._sessions: Dict[str, SessionEntry] = {}
        self._lock = Lock()

    def get_or_create(self, user_id: str) -> AppointmentFSM:
        now = datetime.utcnow()
        with self._lock:
            self._cleanup(now)
            entry = self._sessions.get(user_id)
            if entry is None:
                fsm = self._load_or_create_fsm(user_id=user_id)
                entry = SessionEntry(fsm=fsm, last_seen_utc=now)
                self._sessions[user_id] = entry
            else:
                entry.last_seen_utc = now
            return entry.fsm

    def save(self, user_id: str) -> None:
        if not self.conversation_repository:
            return
        with self._lock:
            entry = self._sessions.get(user_id)
            if not entry:
                return
            fsm = entry.fsm
            self.conversation_repository.save_session(
                user_id=user_id,
                state=fsm.state,
                context=fsm.context.__dict__,
                response_language=fsm.response_language,
                language_locked=fsm.language_locked,
                language_turn_count=fsm.language_turn_count,
                init_unclear_count=fsm.init_unclear_count,
                in_edit_flow=fsm.in_edit_flow,
                doctor_id=fsm.doctor_id,
                admin_id=fsm.admin_id,
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
        if not self.conversation_repository:
            return fsm
        snapshot = self.conversation_repository.load_session(user_id=user_id, ttl_minutes=int(self.ttl.total_seconds() // 60))
        if not snapshot:
            return fsm
        try:
            context_payload = json.loads(snapshot.context_json or "{}")
            for key, value in context_payload.items():
                if hasattr(fsm.context, key):
                    setattr(fsm.context, key, value)
            fsm.state = snapshot.state or "INIT"
            fsm.response_language = snapshot.response_language or "en"
            fsm.language_locked = bool(snapshot.language_locked)
            fsm.language_turn_count = int(snapshot.language_turn_count or 0)
            fsm.init_unclear_count = int(snapshot.init_unclear_count or 0)
            fsm.in_edit_flow = bool(snapshot.in_edit_flow)
            fsm.doctor_id = snapshot.doctor_id
            fsm.admin_id = snapshot.admin_id
        except Exception:
            return fsm
        return fsm

    def _cleanup(self, now: datetime) -> None:
        expired = [
            user_id
            for user_id, entry in self._sessions.items()
            if now - entry.last_seen_utc > self.ttl
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)
