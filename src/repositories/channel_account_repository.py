from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from src.db.connection import MySQLConfig, connect_mysql


@dataclass
class ChannelAccount:
    channel_account_id: int
    admin_id: Optional[int]
    channel: str
    provider: str
    sender_identity: str
    webhook_path_key: str
    webhook_secret_enc: str
    credential_json_enc: str
    status: str
    is_primary: bool

    def credentials(self) -> dict:
        raw = (self.credential_json_enc or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


class ChannelAccountRepository:
    def __init__(self, config: MySQLConfig) -> None:
        self.config = config

    def _connect(self):
        return connect_mysql(self.config)

    @staticmethod
    def _normalize_sender_identity(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        lowered = raw.lower()
        if lowered.startswith("whatsapp:"):
            lowered = lowered[len("whatsapp:") :].strip()
        digits = "".join(ch for ch in lowered if ch.isdigit())
        if digits:
            if len(digits) == 10:
                return f"+91{digits}"
            if not digits.startswith("+"):
                return f"+{digits}"
        return raw.strip()

    def get_account_by_id(self, channel_account_id: int) -> Optional[ChannelAccount]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    channel_account_id,
                    admin_id,
                    channel,
                    provider,
                    sender_identity,
                    webhook_path_key,
                    webhook_secret_enc,
                    credential_json_enc,
                    status,
                    is_primary
                FROM channel_accounts
                WHERE channel_account_id = %s
                  AND status = 'ACTIVE'
                LIMIT 1
                """,
                (int(channel_account_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ChannelAccount(
                channel_account_id=int(row["channel_account_id"]),
                admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                channel=str(row.get("channel") or "").strip().lower(),
                provider=str(row.get("provider") or "").strip().lower(),
                sender_identity=str(row.get("sender_identity") or "").strip(),
                webhook_path_key=str(row.get("webhook_path_key") or "").strip(),
                webhook_secret_enc=str(row.get("webhook_secret_enc") or "").strip(),
                credential_json_enc=str(row.get("credential_json_enc") or "").strip(),
                status=str(row.get("status") or "").strip().upper(),
                is_primary=bool(row.get("is_primary")),
            )
        finally:
            cur.close()
            conn.close()

    def resolve_by_webhook_key(
        self,
        *,
        channel: str,
        webhook_key: str,
        webhook_secret: str = "",
    ) -> Optional[ChannelAccount]:
        if not webhook_key:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    channel_account_id,
                    admin_id,
                    channel,
                    provider,
                    sender_identity,
                    webhook_path_key,
                    webhook_secret_enc,
                    credential_json_enc,
                    status,
                    is_primary
                FROM channel_accounts
                WHERE channel = %s
                  AND webhook_path_key = %s
                  AND status = 'ACTIVE'
                LIMIT 1
                """,
                ((channel or "").strip().lower(), webhook_key.strip()),
            )
            row = cur.fetchone()
            if not row:
                return None
            if row.get("webhook_secret_enc"):
                expected = str(row.get("webhook_secret_enc") or "").strip()
                if expected and expected != (webhook_secret or "").strip():
                    return None
            return ChannelAccount(
                channel_account_id=int(row["channel_account_id"]),
                admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                channel=str(row.get("channel") or "").strip().lower(),
                provider=str(row.get("provider") or "").strip().lower(),
                sender_identity=str(row.get("sender_identity") or "").strip(),
                webhook_path_key=str(row.get("webhook_path_key") or "").strip(),
                webhook_secret_enc=str(row.get("webhook_secret_enc") or "").strip(),
                credential_json_enc=str(row.get("credential_json_enc") or "").strip(),
                status=str(row.get("status") or "").strip().upper(),
                is_primary=bool(row.get("is_primary")),
            )
        finally:
            cur.close()
            conn.close()

    def resolve_by_sender_identity(self, *, channel: str, sender_identity: str) -> Optional[ChannelAccount]:
        normalized = self._normalize_sender_identity(sender_identity)
        if not normalized:
            return None
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    channel_account_id,
                    admin_id,
                    channel,
                    provider,
                    sender_identity,
                    webhook_path_key,
                    webhook_secret_enc,
                    credential_json_enc,
                    status,
                    is_primary
                FROM channel_accounts
                WHERE channel = %s
                  AND status = 'ACTIVE'
                """,
                ((channel or "").strip().lower(),),
            )
            rows = cur.fetchall() or []
            for row in rows:
                db_identity = self._normalize_sender_identity(str(row.get("sender_identity") or ""))
                if db_identity and db_identity == normalized:
                    return ChannelAccount(
                        channel_account_id=int(row["channel_account_id"]),
                        admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                        channel=str(row.get("channel") or "").strip().lower(),
                        provider=str(row.get("provider") or "").strip().lower(),
                        sender_identity=str(row.get("sender_identity") or "").strip(),
                        webhook_path_key=str(row.get("webhook_path_key") or "").strip(),
                        webhook_secret_enc=str(row.get("webhook_secret_enc") or "").strip(),
                        credential_json_enc=str(row.get("credential_json_enc") or "").strip(),
                        status=str(row.get("status") or "").strip().upper(),
                        is_primary=bool(row.get("is_primary")),
                    )
            return None
        finally:
            cur.close()
            conn.close()

    def resolve_binding(self, channel_account_id: int) -> Optional[dict]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT doctor_id, clinic_id, channel_account_id
                FROM doctor_channel_bindings
                WHERE channel_account_id = %s
                  AND status = 'ACTIVE'
                ORDER BY is_primary DESC, binding_id ASC
                LIMIT 1
                """,
                (int(channel_account_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            doctor_id = int(row["doctor_id"]) if row.get("doctor_id") is not None else None
            if doctor_id is None:
                return None
            cur.execute(
                """
                SELECT admin_id
                FROM doctors
                WHERE doctor_id = %s
                LIMIT 1
                """,
                (doctor_id,),
            )
            drow = cur.fetchone() or {}
            admin_id = int(drow["admin_id"]) if drow.get("admin_id") is not None else None
            return {
                "doctor_id": doctor_id,
                "admin_id": admin_id,
                "clinic_id": int(row["clinic_id"]) if row.get("clinic_id") is not None else None,
                "channel_account_id": int(row["channel_account_id"]),
            }
        finally:
            cur.close()
            conn.close()

    def resolve_account_for_doctor(self, *, channel: str, doctor_id: int) -> Optional[ChannelAccount]:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT
                    ca.channel_account_id,
                    ca.admin_id,
                    ca.channel,
                    ca.provider,
                    ca.sender_identity,
                    ca.webhook_path_key,
                    ca.webhook_secret_enc,
                    ca.credential_json_enc,
                    ca.status,
                    ca.is_primary
                FROM doctor_channel_bindings b
                JOIN channel_accounts ca ON ca.channel_account_id = b.channel_account_id
                WHERE b.doctor_id = %s
                  AND b.status = 'ACTIVE'
                  AND ca.status = 'ACTIVE'
                  AND ca.channel = %s
                ORDER BY b.is_primary DESC, ca.is_primary DESC, b.binding_id ASC
                LIMIT 1
                """,
                (int(doctor_id), (channel or "").strip().lower()),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ChannelAccount(
                channel_account_id=int(row["channel_account_id"]),
                admin_id=int(row["admin_id"]) if row.get("admin_id") is not None else None,
                channel=str(row.get("channel") or "").strip().lower(),
                provider=str(row.get("provider") or "").strip().lower(),
                sender_identity=str(row.get("sender_identity") or "").strip(),
                webhook_path_key=str(row.get("webhook_path_key") or "").strip(),
                webhook_secret_enc=str(row.get("webhook_secret_enc") or "").strip(),
                credential_json_enc=str(row.get("credential_json_enc") or "").strip(),
                status=str(row.get("status") or "").strip().upper(),
                is_primary=bool(row.get("is_primary")),
            )
        finally:
            cur.close()
            conn.close()

    def current_route_cache_version(self) -> int:
        conn = self._connect()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT version
                FROM route_cache_versions
                WHERE entity = 'channel_routing'
                LIMIT 1
                """
            )
            row = cur.fetchone() or {}
            value = int(row.get("version") or 1)
            return value if value > 0 else 1
        except Exception:
            return 1
        finally:
            cur.close()
            conn.close()

    def bump_route_cache_version(self) -> int:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE
                    version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
            conn.commit()
            cur.execute(
                """
                SELECT version
                FROM route_cache_versions
                WHERE entity = 'channel_routing'
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if isinstance(row, (list, tuple)) and row:
                return max(1, int(row[0] or 1))
            if isinstance(row, dict):
                return max(1, int(row.get("version") or 1))
            return 1
        except Exception:
            conn.rollback()
            return 1
        finally:
            cur.close()
            conn.close()
