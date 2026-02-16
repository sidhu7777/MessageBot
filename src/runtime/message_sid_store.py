import json
import os
import threading
import time
from collections import deque


class PersistentMessageSidStore:
    def __init__(self, path: str, max_entries: int = 50000) -> None:
        self._path = path
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._ordered: deque[str] = deque()
        self._seen: set[str] = set()
        self._dirty_count = 0
        self._load()

    def _load(self) -> None:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    sid = str(row.get("sid", "")).strip()
                except Exception:
                    sid = line
                if sid and sid not in self._seen:
                    self._seen.add(sid)
                    self._ordered.append(sid)
        self._trim_if_needed(force=True)

    def seen_or_add(self, sid: str) -> bool:
        normalized = (sid or "").strip()
        if not normalized:
            return False
        with self._lock:
            if normalized in self._seen:
                return True
            self._seen.add(normalized)
            self._ordered.append(normalized)
            self._append(normalized)
            self._dirty_count += 1
            self._trim_if_needed(force=False)
            return False

    def size(self) -> int:
        with self._lock:
            return len(self._seen)

    def _append(self, sid: str) -> None:
        row = {"sid": sid, "ts": int(time.time())}
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _trim_if_needed(self, force: bool) -> None:
        if len(self._ordered) <= self._max_entries:
            return
        # Rewrite file when crossing cap (or periodic force), keeping newest entries only.
        while len(self._ordered) > self._max_entries:
            dropped = self._ordered.popleft()
            self._seen.discard(dropped)
        # Avoid rewriting too often.
        if not force and self._dirty_count < 100:
            return
        rows = [{"sid": sid, "ts": int(time.time())} for sid in self._ordered]
        with open(self._path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._dirty_count = 0
