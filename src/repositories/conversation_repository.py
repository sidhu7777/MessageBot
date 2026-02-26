import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import mysql.connector

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class SessionSnapshot:
    user_id: str
    state: str
    context_json: str
    response_language: str
    language_locked: bool
    language_turn_count: int
    init_unclear_count: int
    in_edit_flow: bool
    doctor_id: Optional[int]
    admin_id: Optional[int]
    updated_at: datetime


@dataclass
class QueuedTurn:
    queue_id: int
    inbound_sid: str
    from_number: str
    body: str
    pre_state: str
    attempt_count: int


class ConversationRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._schema_ready = False

    def _connect(self):
        return connect_mysql(self._config)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    user_id VARCHAR(64) PRIMARY KEY,
                    state VARCHAR(64) NOT NULL,
                    context_json TEXT NOT NULL,
                    response_language VARCHAR(16) NOT NULL,
                    language_locked TINYINT(1) NOT NULL DEFAULT 0,
                    language_turn_count INT NOT NULL DEFAULT 0,
                    init_unclear_count INT NOT NULL DEFAULT 0,
                    in_edit_flow TINYINT(1) NOT NULL DEFAULT 0,
                    doctor_id INT NULL,
                    admin_id INT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_message_sids (
                    message_sid VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    body TEXT NULL,
                    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_turn_queue (
                    queue_id BIGINT NOT NULL AUTO_INCREMENT,
                    inbound_sid VARCHAR(96) NOT NULL,
                    from_number VARCHAR(80) NOT NULL,
                    body TEXT NOT NULL,
                    pre_state VARCHAR(64) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    attempt_count INT NOT NULL DEFAULT 0,
                    next_retry_at DATETIME NULL DEFAULT NULL,
                    locked_at DATETIME NULL,
                    lock_owner VARCHAR(80) NULL,
                    last_error TEXT NULL,
                    dead_at DATETIME NULL,
                    dead_reason TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (queue_id),
                    UNIQUE KEY uq_inbound_turn_sid (inbound_sid),
                    KEY idx_inbound_turn_poll (status, next_retry_at),
                    KEY idx_inbound_turn_lock (locked_at, lock_owner)
                ) ENGINE=InnoDB
                """
            )
            conn.commit()
            self._schema_ready = True
        finally:
            cur.close()
            conn.close()

    def seen_or_add_message_sid(self, message_sid: str, user_id: str, body: str) -> bool:
        if not message_sid:
            return False
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO inbound_message_sids (message_sid, user_id, body)
                VALUES (%s, %s, %s)
                """,
                (message_sid, user_id, body),
            )
            conn.commit()
            return False
        except mysql.connector.errors.IntegrityError:
            conn.rollback()
            return True
        finally:
            cur.close()
            conn.close()

    def dedup_size(self) -> int:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT COUNT(*) AS c FROM inbound_message_sids")
            row = cur.fetchone()
            return int(row["c"] if row else 0)
        finally:
            cur.close()
            conn.close()

    def load_session(self, user_id: str, ttl_minutes: int) -> Optional[SessionSnapshot]:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    user_id,
                    state,
                    context_json,
                    response_language,
                    language_locked,
                    language_turn_count,
                    init_unclear_count,
                    in_edit_flow,
                    doctor_id,
                    admin_id,
                    updated_at
                FROM conversation_sessions
                WHERE user_id = %s
                  AND updated_at >= (NOW() - INTERVAL %s MINUTE)
                LIMIT 1
                """,
                (user_id, ttl_minutes),
            )
            row = cur.fetchone()
            if not row:
                return None
            return SessionSnapshot(
                user_id=row["user_id"],
                state=row["state"],
                context_json=row["context_json"] or "{}",
                response_language=row["response_language"] or "en",
                language_locked=bool(row["language_locked"]),
                language_turn_count=int(row["language_turn_count"] or 0),
                init_unclear_count=int(row["init_unclear_count"] or 0),
                in_edit_flow=bool(row["in_edit_flow"]),
                doctor_id=int(row["doctor_id"]) if row["doctor_id"] is not None else None,
                admin_id=int(row["admin_id"]) if row["admin_id"] is not None else None,
                updated_at=row["updated_at"],
            )
        finally:
            cur.close()
            conn.close()

    def save_session(
        self,
        *,
        user_id: str,
        state: str,
        context: dict,
        response_language: str,
        language_locked: bool,
        language_turn_count: int,
        init_unclear_count: int,
        in_edit_flow: bool,
        doctor_id: Optional[int],
        admin_id: Optional[int],
    ) -> None:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO conversation_sessions (
                    user_id, state, context_json, response_language,
                    language_locked, language_turn_count, init_unclear_count,
                    in_edit_flow, doctor_id, admin_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    state = VALUES(state),
                    context_json = VALUES(context_json),
                    response_language = VALUES(response_language),
                    language_locked = VALUES(language_locked),
                    language_turn_count = VALUES(language_turn_count),
                    init_unclear_count = VALUES(init_unclear_count),
                    in_edit_flow = VALUES(in_edit_flow),
                    doctor_id = VALUES(doctor_id),
                    admin_id = VALUES(admin_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    state,
                    json.dumps(context, ensure_ascii=False),
                    response_language,
                    1 if language_locked else 0,
                    int(language_turn_count),
                    int(init_unclear_count),
                    1 if in_edit_flow else 0,
                    doctor_id,
                    admin_id,
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def enqueue_overflow_turn(
        self,
        *,
        inbound_sid: str,
        from_number: str,
        body: str,
        pre_state: str,
    ) -> None:
        normalized_sid = (inbound_sid or "").strip()
        if not normalized_sid:
            normalized_sid = f"NO_SID:{from_number}:{int(datetime.utcnow().timestamp())}"
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO inbound_turn_queue
                (inbound_sid, from_number, body, pre_state, status, next_retry_at, locked_at, lock_owner, dead_at, dead_reason)
                VALUES (%s, %s, %s, %s, 'PENDING', UTC_TIMESTAMP(), NULL, NULL, NULL, NULL)
                ON DUPLICATE KEY UPDATE
                    from_number = VALUES(from_number),
                    body = VALUES(body),
                    pre_state = VALUES(pre_state),
                    status = CASE WHEN status = 'DEAD' THEN status ELSE 'PENDING' END,
                    next_retry_at = CASE WHEN status = 'DEAD' THEN next_retry_at ELSE UTC_TIMESTAMP() END,
                    locked_at = NULL,
                    lock_owner = NULL
                """,
                (
                    normalized_sid,
                    (from_number or "").strip(),
                    body or "",
                    (pre_state or "INIT").strip(),
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def claim_overflow_turns(self, *, limit: int, worker_id: str) -> list[QueuedTurn]:
        self.ensure_schema()
        conn = self._connect()
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        try:
            safe_limit = max(1, min(200, int(limit)))
            rows: list[dict] = []
            try:
                cur.execute(
                    """
                    SELECT queue_id, inbound_sid, from_number, body, pre_state, attempt_count
                    FROM inbound_turn_queue
                    WHERE status IN ('PENDING', 'RETRY')
                      AND dead_at IS NULL
                      AND next_retry_at <= UTC_TIMESTAMP()
                      AND (locked_at IS NULL OR locked_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 MINUTE))
                    ORDER BY queue_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (safe_limit,),
                )
                rows = cur.fetchall()
            except Exception:
                cur.execute(
                    """
                    SELECT queue_id, inbound_sid, from_number, body, pre_state, attempt_count
                    FROM inbound_turn_queue
                    WHERE status IN ('PENDING', 'RETRY')
                      AND dead_at IS NULL
                      AND next_retry_at <= UTC_TIMESTAMP()
                      AND (locked_at IS NULL OR locked_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 MINUTE))
                    ORDER BY queue_id
                    LIMIT %s
                    FOR UPDATE
                    """,
                    (safe_limit,),
                )
                rows = cur.fetchall()

            if not rows:
                conn.commit()
                return []

            ids = [int(r["queue_id"]) for r in rows]
            placeholders = ", ".join(["%s"] * len(ids))
            cur.execute(
                f"""
                UPDATE inbound_turn_queue
                SET status = 'PROCESSING',
                    locked_at = UTC_TIMESTAMP(),
                    lock_owner = %s
                WHERE queue_id IN ({placeholders})
                """,
                tuple([worker_id] + ids),
            )
            conn.commit()
            return [
                QueuedTurn(
                    queue_id=int(r["queue_id"]),
                    inbound_sid=str(r.get("inbound_sid") or ""),
                    from_number=str(r.get("from_number") or ""),
                    body=str(r.get("body") or ""),
                    pre_state=str(r.get("pre_state") or "INIT"),
                    attempt_count=int(r.get("attempt_count") or 0),
                )
                for r in rows
            ]
        finally:
            cur.close()
            conn.close()

    def mark_overflow_turn_done(self, *, queue_id: int) -> None:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE inbound_turn_queue
                SET status = 'DONE',
                    locked_at = NULL,
                    lock_owner = NULL,
                    last_error = NULL
                WHERE queue_id = %s
                """,
                (int(queue_id),),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def mark_overflow_turn_retry(
        self,
        *,
        queue_id: int,
        error_text: str,
        backoff_seconds: int,
        max_attempts: int,
    ) -> None:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            safe_backoff = max(1, int(backoff_seconds))
            safe_max_attempts = max(1, int(max_attempts))
            cur.execute(
                """
                UPDATE inbound_turn_queue
                SET
                    attempt_count = attempt_count + 1,
                    status = CASE WHEN (attempt_count + 1) >= %s THEN 'DEAD' ELSE 'RETRY' END,
                    next_retry_at = CASE WHEN (attempt_count + 1) >= %s THEN next_retry_at ELSE DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND) END,
                    last_error = %s,
                    dead_at = CASE WHEN (attempt_count + 1) >= %s THEN UTC_TIMESTAMP() ELSE dead_at END,
                    dead_reason = CASE WHEN (attempt_count + 1) >= %s THEN %s ELSE dead_reason END,
                    locked_at = NULL,
                    lock_owner = NULL
                WHERE queue_id = %s
                """,
                (
                    safe_max_attempts,
                    safe_max_attempts,
                    safe_backoff,
                    (error_text or "").strip() or None,
                    safe_max_attempts,
                    safe_max_attempts,
                    (error_text or "").strip() or "max attempts exceeded",
                    int(queue_id),
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def release_overflow_turn(
        self,
        *,
        queue_id: int,
        reason: str = "",
        backoff_seconds: int = 1,
    ) -> None:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE inbound_turn_queue
                SET status = 'RETRY',
                    next_retry_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
                    last_error = %s,
                    locked_at = NULL,
                    lock_owner = NULL
                WHERE queue_id = %s
                """,
                (max(1, int(backoff_seconds)), (reason or "").strip() or None, int(queue_id)),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def overflow_queue_stats(self) -> dict:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('PENDING', 'RETRY') THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END) AS processing,
                    SUM(CASE WHEN status = 'DEAD' THEN 1 ELSE 0 END) AS dead
                FROM inbound_turn_queue
                """
            )
            row = cur.fetchone() or {}
            return {
                "queued": int(row.get("queued") or 0),
                "processing": int(row.get("processing") or 0),
                "dead": int(row.get("dead") or 0),
            }
        finally:
            cur.close()
            conn.close()

