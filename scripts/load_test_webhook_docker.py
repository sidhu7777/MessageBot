"""
Simple load tester for webhook ACK latency against a running docker stack.

Example:
  python scripts/load_test_webhook_docker.py \
    --url http://localhost:8000/telegram/webhook/<webhook_key> \
    --secret <telegram_secret> \
    --requests 2000 \
    --concurrency 50
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


def _payload(message_id: int, user_id: int, text: str) -> bytes:
    body = {
        "message": {
            "message_id": message_id,
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": user_id},
        }
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    idx = int((pct / 100.0) * len(sorted_values)) - 1
    idx = max(0, min(len(sorted_values) - 1, idx))
    return sorted_values[idx]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Webhook URL, e.g. http://localhost:8000/telegram/webhook/doc42")
    parser.add_argument("--secret", default="", help="Telegram secret header value")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=20260319)
    args = parser.parse_args()

    total = max(1, int(args.requests))
    concurrency = max(1, int(args.concurrency))
    timeout_seconds = max(0.2, float(args.timeout_seconds))
    rng = random.Random(int(args.seed))
    rng_lock = threading.Lock()

    latencies_ms: list[float] = []
    statuses: dict[int, int] = {}
    failures: list[str] = []
    lock = threading.Lock()

    def run_one(i: int) -> None:
        with rng_lock:
            user_id = rng.randint(100000, 999999)
            suffix = rng.randint(100, 999)
        req = urlrequest.Request(
            url=args.url,
            data=_payload(message_id=1000000 + i, user_id=user_id, text=f"load-{suffix}"),
            headers={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": args.secret,
            },
            method="POST",
        )
        t0 = time.perf_counter_ns()
        try:
            with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:
                status = int(getattr(resp, "status", 200))
                _ = resp.read()
        except HTTPError as exc:
            status = int(exc.code or 500)
        except URLError as exc:
            with lock:
                failures.append(f"URLError: {exc}")
            return
        except Exception as exc:
            with lock:
                failures.append(f"Error: {exc}")
            return
        dt_ms = (time.perf_counter_ns() - t0) / 1e6
        with lock:
            latencies_ms.append(dt_ms)
            statuses[status] = int(statuses.get(status, 0) + 1)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(run_one, i) for i in range(total)]
        for _ in as_completed(futures):
            pass
    elapsed = max(0.001, time.perf_counter() - started)

    ok = len(latencies_ms)
    latencies_ms.sort()
    result = {
        "target_url": args.url,
        "total_requests": total,
        "completed_requests": ok,
        "failed_requests": total - ok,
        "duration_seconds": round(elapsed, 3),
        "throughput_rps": round(ok / elapsed, 2),
        "status_counts": statuses,
        "latency_ms": {
            "min": round(latencies_ms[0], 3) if latencies_ms else None,
            "p50": round(_percentile(latencies_ms, 50), 3) if latencies_ms else None,
            "p95": round(_percentile(latencies_ms, 95), 3) if latencies_ms else None,
            "p99": round(_percentile(latencies_ms, 99), 3) if latencies_ms else None,
            "max": round(latencies_ms[-1], 3) if latencies_ms else None,
            "mean": round(statistics.mean(latencies_ms), 3) if latencies_ms else None,
        },
        "failures_sample": failures[:10],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if ok == 0:
        return 2
    if result["latency_ms"]["p95"] and result["latency_ms"]["p95"] > 200:
        # Non-fatal, but signals tuning needed.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

