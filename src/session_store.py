from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Dict, Optional

from src.db_store import BookingRepository
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient


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
        enable_response_polish: bool = False,
        booking_repository: Optional[BookingRepository] = None,
        ttl_minutes: int = 120,
    ) -> None:
        self.llm_client = llm_client
        self.mixed_response_language = mixed_response_language
        self.enable_llm_polish = enable_llm_polish
        self.enable_response_polish = enable_response_polish
        self.booking_repository = booking_repository
        self.ttl = timedelta(minutes=ttl_minutes)
        self._sessions: Dict[str, SessionEntry] = {}
        self._lock = Lock()

    def get_or_create(self, user_id: str) -> AppointmentFSM:
        now = datetime.utcnow()
        with self._lock:
            self._cleanup(now)
            entry = self._sessions.get(user_id)
            if entry is None:
                entry = SessionEntry(
                    fsm=AppointmentFSM(
                        llm_client=self.llm_client,
                        mixed_response_language=self.mixed_response_language,
                        enable_llm_polish=self.enable_llm_polish,
                        enable_response_polish=self.enable_response_polish,
                        booking_repository=self.booking_repository,
                        chat_phone_number=user_id,
                    ),
                    last_seen_utc=now,
                )
                self._sessions[user_id] = entry
            else:
                entry.last_seen_utc = now
            return entry.fsm

    def _cleanup(self, now: datetime) -> None:
        expired = [
            user_id
            for user_id, entry in self._sessions.items()
            if now - entry.last_seen_utc > self.ttl
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)
