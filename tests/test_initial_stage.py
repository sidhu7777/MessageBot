import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient
from src.nlu.extractors import is_availability_intent, is_booking_intent, is_greeting_intent
from src.nlu.initial_router import route_initial_decision
from src.nlu.language_detector import detect_language

DATA_DIR = ROOT / "testdata"
REPORT_DIR = ROOT / "reports"
TEST_LLM_TIMEOUT_SECONDS = 300.0
INIT_CASE_LIMIT = int(os.getenv("INIT_CASE_LIMIT", "80"))


def _new_llm_client() -> LLMClient:
    return LLMClient(model="qwen3:0.6b", timeout_seconds=TEST_LLM_TIMEOUT_SECONDS)


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _route_rule_only(lower: str) -> str | None:
    normalized = lower.strip()
    if normalized in {"1", "option 1", "book now", "booking"} or normalized.startswith("1 "):
        return "BOOK_APPOINTMENT"
    if normalized in {"2", "option 2", "check availability"} or normalized.startswith("2 "):
        return "CHECK_AVAILABILITY"
    if is_availability_intent(lower):
        return "CHECK_AVAILABILITY"
    if is_booking_intent(lower):
        return "BOOK_APPOINTMENT"
    if is_greeting_intent(lower):
        return "GREETING"
    return None


def run_init_case_tests() -> tuple[int, int, dict]:
    rows = _load_jsonl(DATA_DIR / "init_cases.jsonl")
    if INIT_CASE_LIMIT > 0:
        rows = rows[:INIT_CASE_LIMIT]
    llm = _new_llm_client()

    passed = 0
    failed = 0
    stats = Counter()
    failures: list[dict] = []

    for idx, row in enumerate(rows, start=1):
        text = row["text"]
        lower = text.lower()
        expected_intent = row["expected_intent"]
        expected_language = row["expected_language"]

        rule_intent = _route_rule_only(lower)
        intent, detected_lang = route_initial_decision(
            llm_client=llm,
            enable_llm_polish=True,
            text=text,
            lower=lower,
        )

        rule_language = detect_language(lower) or "none"
        language = detected_lang or rule_language

        if rule_intent is None:
            stats["intent_rule_abstain"] += 1
            if intent == expected_intent:
                stats["intent_llm_recovered_after_rule_abstain"] += 1
            else:
                stats["intent_llm_failed_after_rule_abstain"] += 1
        else:
            if rule_intent == expected_intent:
                stats["intent_rule_direct_correct"] += 1
            else:
                stats["intent_rule_direct_incorrect"] += 1

        if rule_language == "none":
            stats["language_rule_abstain"] += 1
            if language == expected_language:
                stats["language_llm_recovered_after_rule_abstain"] += 1
            else:
                stats["language_llm_failed_after_rule_abstain"] += 1
        else:
            if rule_language == expected_language:
                stats["language_rule_direct_correct"] += 1
            else:
                stats["language_rule_direct_incorrect"] += 1

        stats[f"intent_expected_{expected_intent}"] += 1
        stats[f"intent_predicted_{intent}"] += 1
        stats[f"language_expected_{expected_language}"] += 1
        stats[f"language_predicted_{language}"] += 1

        ok = intent == expected_intent and language == expected_language
        if ok:
            passed += 1
        else:
            failed += 1
            safe_text = text.encode("unicode_escape").decode("ascii")
            failures.append(
                {
                    "index": idx,
                    "text_escaped": safe_text,
                    "expected_intent": expected_intent,
                    "predicted_intent": intent,
                    "rule_only_intent": rule_intent or "NONE",
                    "expected_language": expected_language,
                    "predicted_language": language,
                    "rule_only_language": rule_language,
                }
            )
            print(
                f"[FAIL init_case #{idx}] text={safe_text!r} "
                f"intent={intent} expected_intent={expected_intent} "
                f"lang={language} expected_lang={expected_language}"
            )

    report = {
        "summary": {
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "accuracy_pct": round((passed / (passed + failed)) * 100, 2) if (passed + failed) else 0.0,
        },
        "diagnostics": dict(stats),
        "failures": failures,
    }
    return passed, failed, report


def run_dialogue_tests() -> tuple[int, int]:
    scenarios = json.loads((DATA_DIR / "init_dialogues.json").read_text(encoding="utf-8"))

    passed = 0
    failed = 0
    for scenario in scenarios:
        fsm = AppointmentFSM(
            llm_client=_new_llm_client(),
            enable_llm_polish=False,
            mixed_response_language="auto",
        )
        reply = ""
        for turn in scenario["turns"]:
            reply = fsm.handle(turn)

        expected_state = scenario["expected_final_state"]
        expected_reply_contains = scenario["expected_reply_contains"].lower()
        ok = fsm.state == expected_state and expected_reply_contains in reply.lower()
        if ok:
            passed += 1
        else:
            failed += 1
            safe_reply = reply.encode("unicode_escape").decode("ascii")
            print(
                f"[FAIL dialogue {scenario['name']}] "
                f"state={fsm.state} expected_state={expected_state} "
                f"reply={safe_reply!r}"
            )
    return passed, failed


def main() -> int:
    passed_1, failed_1, init_report = run_init_case_tests()
    passed_2, failed_2 = run_dialogue_tests()

    passed = passed_1 + passed_2
    failed = failed_1 + failed_2
    total = passed + failed

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "init_detailed_report.json"
    report_path.write_text(json.dumps(init_report, ensure_ascii=False, indent=2), encoding="utf-8")

    d = init_report["diagnostics"]
    print(f"Initial-stage tests: passed={passed} failed={failed} total={total}")
    print(
        "Intent: "
        f"rule_direct_correct={d.get('intent_rule_direct_correct', 0)} "
        f"rule_direct_incorrect={d.get('intent_rule_direct_incorrect', 0)} "
        f"rule_abstain={d.get('intent_rule_abstain', 0)} "
        f"llm_recovered={d.get('intent_llm_recovered_after_rule_abstain', 0)} "
        f"llm_failed={d.get('intent_llm_failed_after_rule_abstain', 0)}"
    )
    print(
        "Language: "
        f"rule_direct_correct={d.get('language_rule_direct_correct', 0)} "
        f"rule_direct_incorrect={d.get('language_rule_direct_incorrect', 0)} "
        f"rule_abstain={d.get('language_rule_abstain', 0)} "
        f"llm_recovered={d.get('language_llm_recovered_after_rule_abstain', 0)} "
        f"llm_failed={d.get('language_llm_failed_after_rule_abstain', 0)}"
    )
    print(f"Detailed report saved: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
