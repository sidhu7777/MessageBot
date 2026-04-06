import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient

DATA_PATH = ROOT / "testdata" / "init_ambiguity_dialogues.json"
REPORT_PATH = ROOT / "reports" / "init_ambiguity_report.json"


def _run_case(case: dict) -> dict:
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", timeout_seconds=300),
        enable_llm_polish=False,
        mixed_response_language="auto",
    )
    reply = ""
    for turn in case["turns"]:
        reply = fsm.handle(turn)

    lower = reply.lower()
    asks_clarify = ("1." in reply and "2." in reply) or (
        "book appointment" in lower and "check doctor availability" in lower
    )

    expected_clarify = bool(case["expected_clarify"])
    expected_state = case.get("expected_state")

    ok = asks_clarify == expected_clarify
    if expected_state is not None:
        ok = ok and (fsm.state == expected_state)

    return {
        "name": case["name"],
        "ok": ok,
        "turns": case["turns"],
        "expected_clarify": expected_clarify,
        "actual_clarify": asks_clarify,
        "expected_state": expected_state,
        "actual_state": fsm.state,
        "final_reply_escaped": reply.encode("unicode_escape").decode("ascii"),
    }


def main() -> int:
    cases = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    results = [_run_case(c) for c in cases]
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "summary": {
                    "passed": passed,
                    "failed": failed,
                    "total": len(results),
                    "accuracy_pct": round((passed / len(results)) * 100, 2),
                },
                "failed_indices_1_based": [i + 1 for i, r in enumerate(results) if not r["ok"]],
                "failed_names": [r["name"] for r in results if not r["ok"]],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Ambiguity fallback tests: passed={passed} failed={failed} total={len(results)}")
    print(f"Detailed report saved: {REPORT_PATH}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

