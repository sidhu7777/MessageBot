import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.llm.client import LLMClient
from src.session_store import SessionManager


def _short(text: str, max_len: int = 100) -> str:
    text = (text or "").replace("\n", " | ")
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def run_parallel_demo() -> int:
    manager = SessionManager(
        llm_client=LLMClient(model="qwen3:0.6b", provider="mock", timeout_seconds=30),
        mixed_response_language="auto",
        enable_llm_polish=False,
        booking_repository=None,
        scheduling_repository=None,
        conversation_repository=None,
        ttl_minutes=120,
    )

    # 4 users in parallel, each with their own chat flow.
    conversations = {
        "whatsapp:+919100000001": [
            "hello",
            "I need to book appointment",
            "my name is Rahul Kumar",
            "1",
            "28",
        ],
        "whatsapp:+919100000002": [
            "hi",
            "doctor availability",
            "Dr Arjun tomorrow",
        ],
        "whatsapp:+919100000003": [
            "hey",
            "I want to meet doctor",
            "book appointment",
            "my name is Sana Ali",
        ],
        "whatsapp:+919100000004": [
            "namaste",
            "book",
            "my name is Kiran Das",
            "2",
            "31",
        ],
    }

    lock = threading.Lock()
    timeline: list[tuple[float, str, int, str, str, str]] = []
    start = time.time()

    def worker(user_id: str, turns: list[str], step_delay: float) -> None:
        for idx, user_text in enumerate(turns, start=1):
            fsm = manager.get_or_create(user_id)
            pre_state = fsm.state
            reply = fsm.handle(user_text)
            post_state = fsm.state
            manager.save(user_id)
            event_time = time.time()
            with lock:
                timeline.append((event_time, user_id, idx, pre_state, post_state, _short(reply)))
                elapsed_ms = int((event_time - start) * 1000)
                print(
                    f"[+{elapsed_ms:>4}ms] {user_id} turn#{idx}: "
                    f"{pre_state} -> {post_state} | {_short(reply)}",
                    flush=True,
                )
            time.sleep(step_delay)

    threads = []
    for i, (user_id, turns) in enumerate(conversations.items()):
        t = threading.Thread(target=worker, args=(user_id, turns, 0.03 + (i * 0.01)), daemon=True)
        threads.append(t)

    print("Parallel Demo Workflow")
    print("=" * 80)
    print(
        f"Users: {len(conversations)} | Total turns: {sum(len(v) for v in conversations.values())}"
    )
    print("")
    print("Live timeline (parallel):")

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    elapsed_ms = int((time.time() - start) * 1000)

    timeline.sort(key=lambda x: x[0])

    print(f"\nElapsed: {elapsed_ms}ms")
    print("\nPer-user timeline:")
    grouped: dict[str, list[tuple[int, str, str, str]]] = {
        user_id: [] for user_id in conversations.keys()
    }
    for _, user_id, turn_no, pre, post, reply in timeline:
        grouped[user_id].append((turn_no, pre, post, reply))

    for user_id in conversations.keys():
        print(f"- {user_id}:")
        for turn_no, pre, post, reply in grouped[user_id]:
            print(f"  turn#{turn_no}: {pre} -> {post} | {reply}")

    print("\nFinal user states:")
    for user_id in conversations.keys():
        fsm = manager.get_or_create(user_id)
        print(f"- {user_id}: state={fsm.state}, lang={fsm.response_language}, patient_name={fsm.context.patient_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_parallel_demo())
