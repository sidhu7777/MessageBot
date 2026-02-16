import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import mysql.connector

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class AuthPrincipal:
    user_id: int
    role: str
    admin_id: Optional[int]
    token: str
    expires_at: datetime


class AuthRepository:
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
                CREATE TABLE IF NOT EXISTS user_tokens (
                    token_hash CHAR(64) PRIMARY KEY,
                    user_id INT NOT NULL,
                    expires_at DATETIME NOT NULL,
                    revoked TINYINT(1) NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_tokens_user (user_id),
                    INDEX idx_user_tokens_expires (expires_at),
                    CONSTRAINT fk_user_tokens_user FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
                """
            )
            conn.commit()
            self._schema_ready = True
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login_admin(self, email: str, password: str, ttl_minutes: int) -> Optional[AuthPrincipal]:
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT user_id, role
                FROM users
                WHERE email = %s AND password = %s
                LIMIT 1
                """,
                (email, password),
            )
            row = cur.fetchone()
            if not row:
                return None

            role = str(row["role"] or "").lower()
            if role not in {"admin", "super_admin"}:
                return None

            user_id = int(row["user_id"])
            cur.execute(
                "SELECT admin_id FROM admins WHERE user_id = %s ORDER BY admin_id LIMIT 1",
                (user_id,),
            )
            admin_row = cur.fetchone()
            admin_id = int(admin_row["admin_id"]) if admin_row else None
            if role == "admin" and admin_id is None:
                return None

            token = secrets.token_urlsafe(40)
            token_hash = self._hash_token(token)
            expires_at = datetime.utcnow() + timedelta(minutes=max(5, ttl_minutes))
            cur.execute(
                """
                INSERT INTO user_tokens (token_hash, user_id, expires_at, revoked)
                VALUES (%s, %s, %s, 0)
                """,
                (token_hash, user_id, expires_at),
            )
            conn.commit()
            return AuthPrincipal(
                user_id=user_id,
                role=role,
                admin_id=admin_id,
                token=token,
                expires_at=expires_at,
            )
        finally:
            cur.close()
            conn.close()

    def validate_token(self, token: str) -> Optional[AuthPrincipal]:
        if not token:
            return None
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            token_hash = self._hash_token(token)
            cur.execute(
                """
                SELECT u.user_id, u.role, ut.expires_at
                FROM user_tokens ut
                JOIN users u ON u.user_id = ut.user_id
                WHERE ut.token_hash = %s
                  AND ut.revoked = 0
                  AND ut.expires_at > UTC_TIMESTAMP()
                LIMIT 1
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if not row:
                return None
            role = str(row["role"] or "").lower()
            if role not in {"admin", "super_admin"}:
                return None
            user_id = int(row["user_id"])
            cur.execute(
                "SELECT admin_id FROM admins WHERE user_id = %s ORDER BY admin_id LIMIT 1",
                (user_id,),
            )
            admin_row = cur.fetchone()
            admin_id = int(admin_row["admin_id"]) if admin_row else None
            return AuthPrincipal(
                user_id=user_id,
                role=role,
                admin_id=admin_id,
                token=token,
                expires_at=row["expires_at"],
            )
        finally:
            cur.close()
            conn.close()

    def revoke_token(self, token: str) -> bool:
        if not token:
            return False
        self.ensure_schema()
        conn = self._connect()
        cur = conn.cursor()
        try:
            token_hash = self._hash_token(token)
            cur.execute(
                """
                UPDATE user_tokens
                SET revoked = 1
                WHERE token_hash = %s AND revoked = 0
                """,
                (token_hash,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            cur.close()
            conn.close()
