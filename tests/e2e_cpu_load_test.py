import json
import os
import queue
import random
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


APP_URL_BASE = "http://127.0.0.1:8000/telegram/webhook"
QUEUE_URL = "http://127.0.0.1:8000/health/queue"
DOCTOR_WEBHOOK_KEYS = ["ltcpu-20260331-doc-%d" % doc_id for doc_id in range(7, 107)]
SCENARIOS = [
    {"name": "10_doctors_x_1_request", "doctors": 10, "patients_per_doctor": 1, "concurrency": 2},
    {"name": "20_doctors_x_1_request", "doctors": 20, "patients_per_doctor": 1, "concurrency": 3},
    {"name": "50_doctors_x_1_request", "doctors": 50, "patients_per_doctor": 1, "concurrency": 5},
]
TEXTS = [
    "need appointment tomorrow morning",
    "book appointment for fever",
    "need doctor consultation today",
    "appointment for headache tomorrow",
    "what slots are available tomorrow",
]


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def http_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.load(resp)


def cpu_percent(interval=1.0):
    def read():
        vals = list(map(int, open("/proc/stat").readline().split()[1:]))
        idle = vals[3] + vals[4]
        total = sum(vals)
        return idle, total

    i1, t1 = read()
    time.sleep(interval)
    i2, t2 = read()
    return round((1 - ((i2 - i1) / max(1, (t2 - t1)))) * 100.0, 2)


def mem_percent():
    total = None
    avail = None
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
    return round((total - avail) * 100.0 / total, 2)


def percentile(values, p):
    if not values:
        return None
    idx = int(len(values) * p) - 1
    idx = max(0, min(len(values) - 1, idx))
    return values[idx]


def build_jobs(stage_idx, doctor_count, patients_per_doctor):
    rng = random.Random(20260331 + stage_idx)
    base_msg_id = int(time.time() * 1000) + stage_idx * 10000000
    jobs = []
    doctors = DOCTOR_WEBHOOK_KEYS[:doctor_count]
    for d_idx, webhook_key in enumerate(doctors):
        for p_idx in range(patients_per_doctor):
            user_id = 930000000 + stage_idx * 1000000 + d_idx * 5000 + p_idx
            message_id = base_msg_id + d_idx * 10000 + p_idx
            text = rng.choice(TEXTS)
            jobs.append((webhook_key, message_id, user_id, text))
    rng.shuffle(jobs)
    return jobs


class AppLogWatcher:
    def __init__(self):
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._proc = None
        self._thread = None

    def start(self):
        self._proc = subprocess.Popen(
            ["docker", "logs", "-f", "--since", "1s", "--timestamps", "message-bot-app"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            self._queue.put(line.rstrip("\n"))

    def poll_lines(self):
        lines = []
        while True:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def post_one(job):
    webhook_key, message_id, user_id, text = job
    payload = {
        "message": {
            "message_id": message_id,
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": user_id},
        }
    }
    req = urllib.request.Request(
        f"{APP_URL_BASE}/{webhook_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = int(getattr(resp, "status", 200))
        _ = resp.read()
    return {
        "sid": str(message_id),
        "status": status,
        "ack_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def run_scenario(stage_idx, scenario):
    before = http_json(QUEUE_URL)["queue"]
    start_utc = now_utc()
    watcher = AppLogWatcher()
    watcher.start()
    jobs = build_jobs(stage_idx, scenario["doctors"], scenario["patients_per_doctor"])
    expected = {str(job[1]) for job in jobs}
    ack_results = []
    start_by_sid = {}
    for _, msg_id, _, _ in jobs:
        start_by_sid[str(msg_id)] = time.time()

    dispatch_started = time.time()
    with ThreadPoolExecutor(max_workers=scenario["concurrency"]) as pool:
        futures = [pool.submit(post_one, job) for job in jobs]
        for fut in as_completed(futures):
            ack_results.append(fut.result())
    dispatch_finished = time.time()

    completion = {}
    max_cpu = 0.0
    max_mem = 0.0
    max_backlog = 0
    unstable = ""
    final_queue = http_json(QUEUE_URL)["queue"]
    monitor_started = time.time()
    while True:
        for line in watcher.poll_lines():
            if "Queued turn processed sid=" in line:
                sid = line.split("Queued turn processed sid=", 1)[1].split()[0]
                if sid in expected and sid not in completion:
                    completion[sid] = {"kind": "processed", "line": line, "done_ts": time.time()}
            elif "Queued turn permanently failed sid=" in line:
                sid = line.split("Queued turn permanently failed sid=", 1)[1].split()[0]
                if sid in expected and sid not in completion:
                    completion[sid] = {"kind": "failed", "line": line, "done_ts": time.time()}

        cpu = cpu_percent(1.0)
        mem = mem_percent()
        q = http_json(QUEUE_URL)["queue"]
        max_cpu = max(max_cpu, cpu)
        max_mem = max(max_mem, mem)
        max_backlog = max(max_backlog, int(q["backlog_size"]))
        final_queue = q
        if len(completion) >= len(expected):
            break
        if int(q["backlog_size"]) >= 60:
            unstable = "queue backlog reached 60"
            break
        if time.time() - monitor_started > 900:
            unstable = "scenario timeout"
            break

    watcher.stop()

    end_utc = now_utc()
    ack_latencies = sorted([r["ack_ms"] for r in ack_results])
    completion_latencies = sorted(
        [round((completion[sid]["done_ts"] - start_by_sid[sid]) * 1000.0, 3) for sid in completion]
    )
    proc = subprocess.run(
        ["journalctl", "-u", "ollama", "--since", start_utc, "--until", end_utc, "--no-pager"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ollama_chat_calls = sum(1 for line in proc.stdout.splitlines() if 'POST     "/api/chat"' in line)

    return {
        "name": scenario["name"],
        "doctors": scenario["doctors"],
        "patients_per_doctor": scenario["patients_per_doctor"],
        "total_requests": len(jobs),
        "concurrency": scenario["concurrency"],
        "dispatch_seconds": round(dispatch_finished - dispatch_started, 3),
        "completion_window_seconds": round(time.time() - dispatch_finished, 3),
        "ack_latency_ms": {
            "p50": percentile(ack_latencies, 0.50),
            "p95": percentile(ack_latencies, 0.95),
            "p99": percentile(ack_latencies, 0.99),
            "max": ack_latencies[-1] if ack_latencies else None,
            "mean": round(sum(ack_latencies) / len(ack_latencies), 3) if ack_latencies else None,
        },
        "completion_latency_ms": {
            "count_completed": len(completion_latencies),
            "p50": percentile(completion_latencies, 0.50),
            "p95": percentile(completion_latencies, 0.95),
            "p99": percentile(completion_latencies, 0.99),
            "max": completion_latencies[-1] if completion_latencies else None,
            "mean": round(sum(completion_latencies) / len(completion_latencies), 3) if completion_latencies else None,
        },
        "completion_kinds": {
            "processed": sum(1 for v in completion.values() if v["kind"] == "processed"),
            "failed": sum(1 for v in completion.values() if v["kind"] == "failed"),
        },
        "queue_delta": {
            "submitted": int(final_queue["submitted"]) - int(before["submitted"]),
            "processed": int(final_queue["processed"]) - int(before["processed"]),
            "retried": int(final_queue["retried"]) - int(before["retried"]),
            "failed": int(final_queue["failed"]) - int(before["failed"]),
            "kafka_published": int(final_queue["kafka_published"]) - int(before["kafka_published"]),
            "kafka_consumed": int(final_queue["kafka_consumed"]) - int(before["kafka_consumed"]),
            "dropped": int(final_queue["dropped"]) - int(before["dropped"]),
        },
        "ollama_chat_calls": ollama_chat_calls,
        "max_cpu_percent": max_cpu,
        "max_mem_percent": max_mem,
        "max_backlog": max_backlog,
        "note": unstable,
    }


def main():
    results = []
    for idx, scenario in enumerate(SCENARIOS, start=1):
        result = run_scenario(idx, scenario)
        results.append(result)
        print(json.dumps(result, indent=2), flush=True)
        if result["note"]:
            break
    print("FINAL_RESULTS=" + json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    main()
