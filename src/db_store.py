import os
from typing import Optional, Tuple

from src.db.connection import MySQLConfig, parse_mysql_url
from src.repositories.auth_repository import AuthRepository
from src.repositories.booking_repository import BookingRepository as CoreBookingRepository
from src.repositories.booking_repository import BookingResult
from src.repositories.conversation_repository import ConversationRepository
from src.repositories.scheduling_repository import SchedulingRepository


class BookingRepository(CoreBookingRepository):
    @classmethod
    def from_env(cls) -> Optional["BookingRepository"]:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url or not database_url.startswith("mysql+mysqlconnector://"):
            return None
        config = parse_mysql_url(database_url)
        return cls(config)


def repositories_from_env() -> Tuple[Optional[BookingRepository], Optional[SchedulingRepository]]:
    booking_repo = BookingRepository.from_env()
    if not booking_repo:
        return None, None
    scheduling_repo = SchedulingRepository(booking_repo._config)  # re-use same parsed config
    return booking_repo, scheduling_repo


def conversation_repository_from_env() -> Optional[ConversationRepository]:
    booking_repo = BookingRepository.from_env()
    if not booking_repo:
        return None
    return ConversationRepository(booking_repo._config)


def auth_repository_from_env() -> Optional[AuthRepository]:
    booking_repo = BookingRepository.from_env()
    if not booking_repo:
        return None
    return AuthRepository(booking_repo._config)


__all__ = [
    "BookingRepository",
    "BookingResult",
    "SchedulingRepository",
    "ConversationRepository",
    "AuthRepository",
    "repositories_from_env",
    "conversation_repository_from_env",
    "auth_repository_from_env",
    "MySQLConfig",
]
