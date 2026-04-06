import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.session_store import SessionManager


class _DummyContext:
    def __init__(self) -> None:
        self.patient_name = ""
        self.phone_number = ""


class _DummyFSM:
    def __init__(self, **kwargs) -> None:
        self.state = "INIT"
        self.context = _DummyContext()
        self.response_language = "en"
        self.language_locked = False
        self.language_turn_count = 0
        self.init_unclear_count = 0
        self.in_edit_flow = False
        self.doctor_id = None
        self.admin_id = None
        self.bot_whatsapp_number = kwargs.get("bot_whatsapp_number", "")
        self.chat_phone_number = kwargs.get("chat_phone_number", "")
        self.known_patient_name = None
        self.pending_init_intent = None
        self.language_selected_by_user = False
        self.booking_for_self = None
        self.selected_time_period = None
        self.time_options_cache = []
        self.time_hour_options_cache = []
        self.time_slot_options_cache = []
        self.time_window_labels_cache = []
        self.availability_date_options_cache = []
        self.in_reschedule_flow = False
        self.pending_existing_action = None
        self.existing_appointment_id = None
        self.existing_booking_clinic_id = None
        self.existing_booking_clinic_name = None
        self.existing_booking_doctor_id = None
        self.existing_booking_old_date = None
        self.existing_booking_old_time = None
        self.active_booking_options_cache = []


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self._data[key] = value
        return True


class _ConversationRepoFail:
    def load_session(self, user_id: str, ttl_minutes: int):
        raise AssertionError("DB fallback should not be used when Redis snapshot exists.")

    def save_session(self, **kwargs):
        return None


def test_session_manager_keeps_users_isolated_via_redis_snapshots() -> None:
    fake_redis = _FakeRedis()
    user_a = "telegram:user_a"
    user_b = "telegram:user_b"
    fake_redis._data[f"msgbot:sess:{user_a}"] = json.dumps(
        {
            "state": "ASK_CLINIC",
            "context": {"patient_name": "Alice", "phone_number": "9000000001"},
            "response_language": "en",
        }
    )
    fake_redis._data[f"msgbot:sess:{user_b}"] = json.dumps(
        {
            "state": "ASK_TIME",
            "context": {"patient_name": "Bob", "phone_number": "9000000002"},
            "response_language": "hi",
        }
    )

    with patch("src.session_store.AppointmentFSM", _DummyFSM):
        manager = SessionManager(
            llm_client=object(),
            conversation_repository=_ConversationRepoFail(),
            redis_client=fake_redis,
            redis_key_prefix="msgbot",
        )
        fsm_a = manager.get_or_create(user_a)
        fsm_b = manager.get_or_create(user_b)

    assert fsm_a.state == "ASK_CLINIC"
    assert fsm_a.context.patient_name == "Alice"
    assert fsm_a.context.phone_number == "9000000001"
    assert fsm_a.response_language == "en"

    assert fsm_b.state == "ASK_TIME"
    assert fsm_b.context.patient_name == "Bob"
    assert fsm_b.context.phone_number == "9000000002"
    assert fsm_b.response_language == "hi"

    assert fsm_a.state != fsm_b.state
    assert fsm_a.context.patient_name != fsm_b.context.patient_name


def test_get_cached_state_reads_redis_only_and_defaults_cleanly() -> None:
    fake_redis = _FakeRedis()
    user_id = "telegram:user_cached"
    fake_redis._data[f"msgbot:sess:{user_id}"] = json.dumps({"state": "ASK_LANGUAGE"})

    with patch("src.session_store.AppointmentFSM", _DummyFSM):
        manager = SessionManager(
            llm_client=object(),
            conversation_repository=_ConversationRepoFail(),
            redis_client=fake_redis,
            redis_key_prefix="msgbot",
        )
        assert manager.get_cached_state(user_id, default="INIT") == "ASK_LANGUAGE"
        assert manager.get_cached_state("telegram:missing", default="INIT") == "INIT"
