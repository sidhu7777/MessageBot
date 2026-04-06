import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.repositories.scheduling_repository import ClinicOption, SchedulingRepository


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key: str):
        self.get_calls += 1
        return self.data.get(key)

    def set(self, key: str, value: str, ex=None):
        self.set_calls += 1
        self.data[key] = value
        return True


class _FlowProbeRepo(SchedulingRepository):
    """Real SchedulingRepository cache flow with deterministic fake DB latency."""

    def __init__(self, redis_client: _FakeRedis) -> None:
        super().__init__(config=object(), redis_client=redis_client, cache_ttl_seconds=3600, cache_key_prefix="msgbot")
        self.db_accept_calls = 0
        self.db_clinic_calls = 0
        self.db_times_calls = 0

    def doctor_accept_days(self, doctor_id: int, admin_id=None) -> int:
        # Keep this DB-like to model cold path cost when snapshot is absent.
        self.db_accept_calls += 1
        time.sleep(0.04)
        return 1

    def _db_list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        self.db_clinic_calls += 1
        time.sleep(0.05)
        return [
            ClinicOption(1, "City Care Clinic", "Delhi", 0),
            ClinicOption(2, "Health Plus Clinic", "Noida", 0),
        ][:limit]

    def _db_list_available_times_for_date(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None):
        self.db_times_calls += 1
        time.sleep(0.04)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        if clinic_id == 2 and slot_date == tomorrow:
            return ["09:00", "09:30", "10:00", "16:30"]
        return []


def _new_fsm(repo: _FlowProbeRepo) -> AppointmentFSM:
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", provider="ollama"),
        enable_llm_polish=False,
        mixed_response_language="auto",
        scheduling_repository=repo,
    )
    fsm.doctor_id = 1
    fsm.admin_id = 1
    return fsm


def test_real_user_flow_cold_vs_warm_availability_latency():
    redis_client = _FakeRedis()
    repo = _FlowProbeRepo(redis_client=redis_client)
    fsm = _new_fsm(repo)

    # User asks availability flow entry (INIT -> ASK_AVAILABILITY_DATE)
    r1 = fsm.handle("2")
    assert fsm.state == "ASK_AVAILABILITY_DATE"
    assert "Please choose a date to check availability:" in r1

    # Cold availability query: should hit DB-backed snapshot build once.
    db_before_cold = (repo.db_accept_calls, repo.db_clinic_calls, repo.db_times_calls)
    t0 = time.perf_counter()
    cold_reply = fsm.handle("1")  # Today
    cold_elapsed = time.perf_counter() - t0
    db_after_cold = (repo.db_accept_calls, repo.db_clinic_calls, repo.db_times_calls)

    assert fsm.state == "ASK_AVAILABILITY_DETAILS"
    assert "No slots available on" in cold_reply
    assert "Next available dates:" in cold_reply
    assert repo._availability_cache_key(doctor_id=1, admin_id=1) in redis_client.data
    assert db_after_cold[1] > db_before_cold[1], "Cold call must build snapshot from DB"

    # Go back to date menu.
    back_reply = fsm.handle("0")
    assert fsm.state == "ASK_AVAILABILITY_DATE"
    assert "Please choose a date to check availability:" in back_reply

    # Warm availability query: should use Redis snapshot (no new DB list calls).
    db_before_warm = (repo.db_accept_calls, repo.db_clinic_calls, repo.db_times_calls)
    t1 = time.perf_counter()
    warm_reply = fsm.handle("1")
    warm_elapsed = time.perf_counter() - t1
    db_after_warm = (repo.db_accept_calls, repo.db_clinic_calls, repo.db_times_calls)

    assert fsm.state == "ASK_AVAILABILITY_DETAILS"
    assert "No slots available on" in warm_reply
    assert db_after_warm == db_before_warm, "Warm call should come from Redis snapshot (no DB rebuild)"
    assert warm_elapsed < cold_elapsed, f"Expected warm latency < cold latency, got warm={warm_elapsed:.4f}s cold={cold_elapsed:.4f}s"
