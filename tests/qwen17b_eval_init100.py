import json
import re
import statistics
import time
import urllib.request
from pathlib import Path

MODEL = "qwen3:1.7b"
API_URL = "http://127.0.0.1:11434/api/chat"
DATA_PATH = Path("tests/testdata/init_cases_100.jsonl")
REPORT_PATH = Path("tests/reports/qwen17b_init100_live_report.txt")

SYSTEM_PROMPT = (
    "You are an intent and language classifier for clinic assistant messages. "
    "Return ONLY strict JSON with keys: intent, language. "
    "intent must be one of: GREETING, BOOK_APPOINTMENT, CHECK_AVAILABILITY, GENERAL_QUERY, OTHER. "
    "language must be one of: en, hi, hinglish."
)

ALLOWED_INTENTS = {"GREETING", "BOOK_APPOINTMENT", "CHECK_AVAILABILITY", "GENERAL_QUERY", "OTHER"}
ALLOWED_LANGS = {"en", "hi", "hinglish"}


def pct(a, b):
    return (a / b * 100.0) if b else 0.0


def load_cases(path: Path):
    rows = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_json(text: str):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}


def infer(text: str):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        url=API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    ms = (time.perf_counter() - t0) * 1000.0

    data = json.loads(raw)
    content = (data.get("message") or {}).get("content", "")
    parsed = parse_json(content)
    pred_intent = str(parsed.get("intent") or "").strip().upper()
    pred_lang = str(parsed.get("language") or "").strip().lower()
    return pred_intent, pred_lang, ms, content


def main():
    cases = load_cases(DATA_PATH)
    n = len(cases)

    intent_ok = 0
    lang_ok = 0
    joint_ok = 0
    processed = 0
    latencies = []
    errors = []

    by_intent = {}
    by_lang = {}

    start = time.perf_counter()
    print(f"Starting eval: model={MODEL}, cases={n}")

    for i, c in enumerate(cases, start=1):
        text = c["text"]
        exp_i = c["expected_intent"].strip().upper()
        exp_l = c["expected_language"].strip().lower()

        by_intent.setdefault(exp_i, [0, 0])[1] += 1
        by_lang.setdefault(exp_l, [0, 0])[1] += 1

        try:
            pi, pl, ms, raw = infer(text)
            processed += 1
            latencies.append(ms)
        except Exception as e:
            errors.append((i, text, f"API_ERROR: {e}"))
            elapsed = time.perf_counter() - start
            avg_ms = statistics.mean(latencies) if latencies else 0.0
            eta_s = ((n - i) * avg_ms / 1000.0) if avg_ms else 0.0
            print(f"[{i}/{n}] FAIL api_error='{e}' elapsed={elapsed:.1f}s eta={eta_s:.1f}s")
            continue

        i_ok = (pi == exp_i)
        l_ok = (pl == exp_l)
        j_ok = (i_ok and l_ok)

        if i_ok:
            intent_ok += 1
            by_intent[exp_i][0] += 1
        if l_ok:
            lang_ok += 1
            by_lang[exp_l][0] += 1
        if j_ok:
            joint_ok += 1

        if pi not in ALLOWED_INTENTS or pl not in ALLOWED_LANGS:
            errors.append((i, text, f"INVALID_OUTPUT intent={pi} language={pl} raw={raw[:160]}"))

        elapsed = time.perf_counter() - start
        avg_ms = statistics.mean(latencies) if latencies else 0.0
        eta_s = ((n - i) * avg_ms / 1000.0) if avg_ms else 0.0

        print(
            f"[{i}/{n}] "
            f"case_ms={ms:.1f} "
            f"intent={'OK' if i_ok else 'MISS'} "
            f"lang={'OK' if l_ok else 'MISS'} "
            f"joint_acc={pct(joint_ok, i):.2f}% "
            f"elapsed={elapsed:.1f}s eta={eta_s:.1f}s"
        )

    total_wall = time.perf_counter() - start
    avg = statistics.mean(latencies) if latencies else 0.0
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = sorted(latencies)[max(0, int(0.95 * len(latencies)) - 1)] if latencies else 0.0

    lines = []
    lines.append("QWEN3:1.7B - LLM ONLY EVAL (init_cases_100)")
    lines.append(f"Total cases: {n}")
    lines.append(f"Processed: {processed}")
    lines.append(f"Errors: {len(errors)}")
    lines.append(f"Intent accuracy: {intent_ok}/{n} = {pct(intent_ok, n):.2f}%")
    lines.append(f"Language accuracy: {lang_ok}/{n} = {pct(lang_ok, n):.2f}%")
    lines.append(f"Joint accuracy: {joint_ok}/{n} = {pct(joint_ok, n):.2f}%")
    lines.append(f"Latency ms avg/p50/p95: {avg:.1f}/{p50:.1f}/{p95:.1f}")
    lines.append(f"Total wall time sec: {total_wall:.2f}")
    lines.append("")
    lines.append("Per intent:")
    for k in sorted(by_intent):
        ok, tot = by_intent[k]
        lines.append(f"  {k}: {ok}/{tot} = {pct(ok, tot):.2f}%")
    lines.append("Per language:")
    for k in sorted(by_lang):
        ok, tot = by_lang[k]
        lines.append(f"  {k}: {ok}/{tot} = {pct(ok, tot):.2f}%")
    lines.append("")
    lines.append("Sample errors:")
    for idx, txt, err in errors[:20]:
        lines.append(f"  #{idx}: {err} | text={txt}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print("\n===== FINAL SUMMARY =====")
    for line in lines[:12]:
        print(line)
    print(f"\nFull report saved to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
