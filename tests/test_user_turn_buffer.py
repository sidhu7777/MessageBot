from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.turn_queue import TurnTask
from src.runtime.user_turn_buffer import UserTurnBuffer


def _task(user: str, sid: str, body: str, state: str, ts: float) -> TurnTask:
    return TurnTask(
        from_number=user,
        inbound_sid=sid,
        body=body,
        pre_state=state,
        enqueue_ts=ts,
    )


def test_keep_latest_on_duplicate_burst_same_state() -> None:
    buf = UserTurnBuffer(max_per_user=5, collapse_window_seconds=6.0)
    user = "telegram:111"
    r1 = buf.push(_task(user, "1", "hi", "INIT", 100.0))
    r2 = buf.push(_task(user, "2", "Hi", "INIT", 101.0))
    assert r1.pending_count == 1
    assert r2.collapsed is True
    assert buf.pending_count(user) == 1
    t = buf.pop_next(user)
    assert t is not None
    assert t.inbound_sid == "2"
    assert t.body == "Hi"


def test_fifo_for_distinct_messages() -> None:
    buf = UserTurnBuffer(max_per_user=5, collapse_window_seconds=6.0)
    user = "telegram:222"
    buf.push(_task(user, "a", "hi", "INIT", 200.0))
    buf.push(_task(user, "b", "appointment", "INIT", 201.0))
    buf.push(_task(user, "c", "1", "ASK_BOOKING_FOR", 202.0))
    t1 = buf.pop_next(user)
    t2 = buf.pop_next(user)
    t3 = buf.pop_next(user)
    t4 = buf.pop_next(user)
    assert t1 is not None and t2 is not None and t3 is not None and t4 is None
    assert [t1.inbound_sid, t2.inbound_sid, t3.inbound_sid] == ["a", "b", "c"]


def test_hard_burst_capacity_and_collapse() -> None:
    buf = UserTurnBuffer(max_per_user=3, collapse_window_seconds=6.0)
    user = "telegram:hard"
    # Burst of near-duplicate greetings should collapse to latest one.
    for i in range(1, 6):
        buf.push(_task(user, f"g{i}", "hello", "INIT", 300.0 + i))
    assert buf.pending_count(user) == 1
    assert buf.pop_next(user).inbound_sid == "g5"

    # Distinct burst should keep FIFO up to capacity and drop oldest.
    for i, body in enumerate(["a", "b", "c", "d", "e"], start=1):
        buf.push(_task(user, f"x{i}", body, "ASK_NAME", 400.0 + i))
    assert buf.pending_count(user) == 3
    t1 = buf.pop_next(user)
    t2 = buf.pop_next(user)
    t3 = buf.pop_next(user)
    assert [t1.inbound_sid, t2.inbound_sid, t3.inbound_sid] == ["x3", "x4", "x5"]


def test_init_intent_priority_keeps_meaningful_message_over_late_noise() -> None:
    buf = UserTurnBuffer(max_per_user=5, collapse_window_seconds=6.0)
    user = "telegram:intent"
    buf.push(_task(user, "1", "hi", "INIT", 100.0))
    buf.push(_task(user, "2", "hello", "INIT", 101.0))
    buf.push(_task(user, "3", "appointment", "INIT", 102.0))
    # Later low-priority noise in same window should not replace booking intent.
    buf.push(_task(user, "4", "hi hcbhcbweh", "INIT", 103.0))
    t1 = buf.pop_next(user)
    t2 = buf.pop_next(user)
    assert t1 is not None and t2 is not None
    # Current buffer policy keeps first meaningful + later meaningful messages.
    assert t1.inbound_sid == "2"
    assert t2.inbound_sid == "3"
    assert "appointment" in t2.body.lower()


def test_non_init_duplicates_are_fifo_not_collapsed() -> None:
    buf = UserTurnBuffer(max_per_user=5, collapse_window_seconds=6.0)
    user = "telegram:fifo"
    buf.push(_task(user, "a1", "1", "ASK_NAME", 200.0))
    buf.push(_task(user, "a2", "1", "ASK_NAME", 201.0))
    assert buf.pending_count(user) == 2
    first = buf.pop_next(user)
    second = buf.pop_next(user)
    assert first is not None and second is not None
    assert [first.inbound_sid, second.inbound_sid] == ["a1", "a2"]
