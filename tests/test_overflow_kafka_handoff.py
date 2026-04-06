import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.background_workers import run_overflow_turn_poll_loop


class _StopAfterOne:
    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def wait(self, _seconds: float) -> None:
        self._set = True


class _FakeConversationRepo:
    def __init__(self) -> None:
        self.rows = [
            SimpleNamespace(
                queue_id=101,
                inbound_sid="sid101",
                from_number="telegram:1",
                body="hello",
                pre_state="INIT",
                attempt_count=0,
            )
        ]
        self.released = []
        self.purged = 0

    def purge_old_message_sids(self, retention_days: int):
        self.purged += 1
        return 0

    def claim_overflow_turns(self, limit: int, worker_id: str):
        rows, self.rows = self.rows, []
        return rows

    def release_overflow_turn(self, *, queue_id: int, reason: str, backoff_seconds: int):
        self.released.append((queue_id, reason, backoff_seconds))


class _FakeKafkaBridge:
    def __init__(self) -> None:
        self.submitted = []

    def submit_overflow(self, task):
        self.submitted.append(task)
        return True


def test_overflow_poll_uses_submit_overflow_when_available() -> None:
    repo = _FakeConversationRepo()
    bridge = _FakeKafkaBridge()
    tracked = []
    stop = _StopAfterOne()

    run_overflow_turn_poll_loop(
        conversation_repository=repo,
        turn_processor=bridge,
        overflow_poll_stop=stop,
        settings=SimpleNamespace(queue_worker_count=1, queue_overflow_requeue_backoff_seconds=1),
        overflow_worker_id="worker-1",
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None),
        sid_retention_days=3,
        sid_purge_interval_seconds=60,
        track_overflow_task=lambda task, queue_id: tracked.append((task.inbound_sid, queue_id)),
    )

    assert len(bridge.submitted) == 1
    assert bridge.submitted[0].inbound_sid == "sid101"
    assert tracked == [("sid101", 101)]
    assert repo.released == []
