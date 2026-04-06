import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository
from src.db.connection import MySQLConfig


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return True

    def scan_iter(self, match: str, count: int = 50):
        prefix = match.rstrip("*")
        for key in list(self.store.keys()):
            if key.startswith(prefix):
                yield key


def _config() -> MySQLConfig:
    return MySQLConfig(user="u", password="p", host="h", port=3306, database="d")


def test_list_available_times_keeps_current_running_today_window_visible(monkeypatch) -> None:
    repo = SchedulingRepository(_config(), redis_client=_FakeRedis())
    today = "2026-03-13"

    monkeypatch.setattr(
        "src.repositories.scheduling_repository.datetime",
        type(
            "_FrozenDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 3, 13, 15, 10)),
            },
        ),
    )

    monkeypatch.setattr(
        repo,
        "_get_availability_snapshot",
        lambda doctor_id, admin_id=None: {
            "times_by_clinic_date": {
                "5|2026-03-13": ["13:00", "14:00", "15:00", "16:00"],
            },
            "time_end_by_clinic_date": {
                "5|2026-03-13": {
                    "13:00": "14:00",
                    "14:00": "15:00",
                    "15:00": "16:00",
                    "16:00": "17:00",
                }
            },
        },
    )
    monkeypatch.setattr(
        repo,
        "_db_list_available_times_for_date",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DB should not be called for cached today slots")),
    )

    times = repo.list_available_times(doctor_id=1, clinic_id=5, slot_date=today, admin_id=1, limit=10)
    assert times == ["15:00", "16:00"]


def test_list_available_dates_filters_out_today_when_only_past_slots_remain(monkeypatch) -> None:
    repo = SchedulingRepository(_config(), redis_client=_FakeRedis())

    monkeypatch.setattr(
        "src.repositories.scheduling_repository.datetime",
        type(
            "_FrozenDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 3, 13, 15, 10)),
            },
        ),
    )

    monkeypatch.setattr(
        repo,
        "_get_availability_snapshot",
        lambda doctor_id, admin_id=None: {
            "dates_by_clinic": {"5": ["2026-03-13", "2026-03-14"]},
            "times_by_clinic_date": {
                "5|2026-03-13": ["13:00", "14:00"],
                "5|2026-03-14": ["10:00"],
            },
            "time_end_by_clinic_date": {
                "5|2026-03-13": {
                    "13:00": "14:00",
                    "14:00": "15:00",
                },
                "5|2026-03-14": {
                    "10:00": "11:00",
                },
            },
        },
    )
    monkeypatch.setattr(repo, "doctor_accept_days", lambda doctor_id, admin_id=None: 1)

    dates = repo.list_available_dates(doctor_id=1, clinic_id=5, admin_id=1, limit=5)
    assert dates == ["2026-03-14"]


def test_list_available_dates_keeps_today_when_current_window_still_active(monkeypatch) -> None:
    repo = SchedulingRepository(_config(), redis_client=_FakeRedis())

    monkeypatch.setattr(
        "src.repositories.scheduling_repository.datetime",
        type(
            "_FrozenDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 3, 13, 15, 20)),
            },
        ),
    )

    monkeypatch.setattr(
        repo,
        "_get_availability_snapshot",
        lambda doctor_id, admin_id=None: {
            "dates_by_clinic": {"5": ["2026-03-13", "2026-03-14"]},
            "times_by_clinic_date": {
                "5|2026-03-13": ["15:00"],
                "5|2026-03-14": ["10:00"],
            },
            "time_end_by_clinic_date": {
                "5|2026-03-13": {
                    "15:00": "16:00",
                },
                "5|2026-03-14": {
                    "10:00": "11:00",
                },
            },
        },
    )
    monkeypatch.setattr(repo, "doctor_accept_days", lambda doctor_id, admin_id=None: 1)

    dates = repo.list_available_dates(doctor_id=1, clinic_id=5, admin_id=1, limit=5)
    assert dates == ["2026-03-13", "2026-03-14"]


def test_patient_name_lookup_uses_redis_after_first_db_hit(monkeypatch) -> None:
    repo = BookingRepository(_config())
    fake_redis = _FakeRedis()
    repo.set_redis_client(fake_redis, key_prefix="msgbot")

    monkeypatch.setattr(repo, "default_admin_id", lambda: 1)
    state = {"db_calls": 0}

    class _Cursor:
        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return {"full_name": "Harshit Patient"}

        def close(self):
            return None

    class _Conn:
        def cursor(self, dictionary=True):
            state["db_calls"] += 1
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(repo, "_connect", lambda: _Conn())

    first = repo.find_patient_name_by_phone_number("9876543210", admin_id=1, doctor_id=1)
    monkeypatch.setattr(
        repo,
        "_connect",
        lambda: (_ for _ in ()).throw(AssertionError("Second lookup should use Redis cache, not DB")),
    )
    second = repo.find_patient_name_by_phone_number("9876543210", admin_id=1, doctor_id=1)

    assert first == "Harshit Patient"
    assert second == "Harshit Patient"
    assert state["db_calls"] == 1
