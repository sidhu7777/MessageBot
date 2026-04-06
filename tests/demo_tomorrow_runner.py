import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.config import load_settings
from src.db_store import repositories_from_env
from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient

DATA_PATH = ROOT / "testdata" / "demo_tomorrow_cases.json"
REPORT_PATH = ROOT / "reports" / "demo_tomorrow_report.json"
SETTINGS = load_settings()
BOOKING_REPO, SCHEDULING_REPO = repositories_from_env()


def run_case(case: dict) -> dict:
    fsm = AppointmentFSM(
        llm_client=LLMClient(model="qwen3:0.6b", timeout_seconds=20),
        mixed_response_language="auto",
        enable_llm_polish=False,
        booking_repository=BOOKING_REPO,
        scheduling_repository=SCHEDULING_REPO,
        chat_phone_number=case.get("chat_phone_number", "whatsapp:+919300000000"),
        bot_whatsapp_number=SETTINGS.twilio_whatsapp_from,
    )
    transcript = []
    last_reply = ""
    for turn in case["turns"]:
        last_reply = fsm.handle(turn)
        transcript.append(
            {
                "user": turn,
                "bot": last_reply,
                "state_after_turn": fsm.state,
            }
        )

    expected = case["expected_final_state"]
    ok = fsm.state == expected
    return {
        "name": case["name"],
        "ok": ok,
        "expected_final_state": expected,
        "actual_final_state": fsm.state,
        "final_reply_escaped": last_reply.encode("unicode_escape").decode("ascii"),
        "transcript": transcript,
    }


def main() -> int:
    cases = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    results = [run_case(case) for case in cases]
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
                    "accuracy_pct": round((passed / len(results)) * 100, 2) if results else 0.0,
                },
                "failed_names": [r["name"] for r in results if not r["ok"]],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Demo tomorrow run: passed={passed} failed={failed} total={len(results)}")
    print(f"Report saved: {REPORT_PATH}")
    print("")
    for result in results:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"[{status}] {result['name']} -> {result['actual_final_state']} (expected {result['expected_final_state']})")
        for turn in result["transcript"]:
            user = turn["user"].encode("unicode_escape").decode("ascii")
            bot = turn["bot"].encode("unicode_escape").decode("ascii")
            print(f"  U: {user}")
            print(f"  B: {bot}")
            print(f"  S: {turn['state_after_turn']}")
        print("")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
