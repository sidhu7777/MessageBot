import os
from typing import Optional, Tuple

from src.db.connection import MySQLConfig, parse_mysql_url
from src.repositories.booking_repository import BookingRepository as CoreBookingRepository
from src.repositories.booking_repository import BookingResult
from src.repositories.conversation_repository import ConversationRepository
from src.repositories.scheduling_repository import SchedulingRepository


class BookingRepository(CoreBookingRepository):
    pass


def repositories_from_env() -> Tuple[Optional[BookingRepository], Optional[SchedulingRepository]]:
    config = _config_from_env()
    if not config:
        return None, None
    booking_repo = BookingRepository(config)
    scheduling_repo = SchedulingRepository(config)
    return booking_repo, scheduling_repo


def conversation_repository_from_env() -> Optional[ConversationRepository]:
    config = _config_from_env()
    if not config:
        return None
    return ConversationRepository(config)


def _config_from_env() -> Optional[MySQLConfig]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url or not database_url.startswith("mysql+mysqlconnector://"):
        return None
    return parse_mysql_url(database_url)


__all__ = [
    "BookingRepository",
    "BookingResult",
    "SchedulingRepository",
    "ConversationRepository",
    "repositories_from_env",
    "conversation_repository_from_env",
    "MySQLConfig",
]
