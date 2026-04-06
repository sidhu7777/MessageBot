import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.session_store import SessionManager


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _DummyContext:
    def __init__(self) -> None:
        self.patient_name = ""
        self.phone = ""


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


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self._data[key] = value
        self.set_calls.append((key, value, int(ex or 0)))
        return True


def test_redis_session_snapshot_read_and_write() -> None:
    user_id = "telegram:111"
    key = f"sess:{user_id}"
    fake_redis = _FakeRedis()
    fake_redis._data[key] = json.dumps(
        {
            "state": "ASK_PHONE",
            "context": {"patient_name": "Vineeth", "phone": "9000000000"},
            "response_language": "en",
            "language_locked": True,
            "language_turn_count": 2,
            "init_unclear_count": 1,
            "in_edit_flow": False,
            "doctor_id": 7,
            "admin_id": 9,
        }
    )

    with patch("src.session_store.AppointmentFSM", _DummyFSM):
        manager = SessionManager(
            llm_client=object(),
            conversation_repository=None,
            redis_client=fake_redis,
            ttl_minutes=10,
        )
        fsm = manager.get_or_create(user_id)
        assert_true(fsm.state == "ASK_PHONE", "FSM state should load from Redis snapshot.")
        assert_true(fsm.context.patient_name == "Vineeth", "Context should load from Redis snapshot.")
        assert_true(fsm.doctor_id == 7 and fsm.admin_id == 9, "Doctor/admin ids should load from Redis.")

        fsm.state = "CONFIRM"
        fsm.context.patient_name = "Updated Name"
        manager.save(user_id, fsm)

    assert_true(len(fake_redis.set_calls) == 1, "Session save should write one Redis snapshot.")
    save_key, raw, ttl_seconds = fake_redis.set_calls[0]
    assert_true(save_key == key, "Redis key must be sess:{user_id}.")
    assert_true(ttl_seconds == 600, "TTL must refresh to 10 minutes (600s).")
    saved = json.loads(raw)
    assert_true(saved.get("state") == "CONFIRM", "Saved snapshot should include updated state.")
    assert_true(saved.get("context", {}).get("patient_name") == "Updated Name", "Saved context must be updated.")


def main() -> int:
    tests = [
        ("redis_session_snapshot_read_and_write", test_redis_session_snapshot_read_and_write),
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"[FAIL] {name}: {exc}")

    print("")
    print(f"Redis session snapshot tests: passed={len(tests)-len(failures)} failed={len(failures)} total={len(tests)}")
    if failures:
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
