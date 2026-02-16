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

