"""
Safe additive migration for multi-channel / multi-bot support.

What it does:
- Creates new tables if missing:
  1) channel_accounts
  2) doctor_channel_bindings
- Adds new columns to existing tables only when missing
- Adds indexes only when missing

What it does NOT do:
- No DROP / DELETE / destructive schema changes
- No data updates

Usage:
  python migrate_multi_channel_schema.py

It reads DATABASE_URL from .env (mysql+mysqlconnector://...).
"""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

import mysql.connector
from dotenv import load_dotenv


def _parse_mysql_url(database_url: str) -> dict[str, object]:
    normalized = database_url.replace("mysql+mysqlconnector://", "mysql://", 1)
    parsed = urlparse(normalized)
    return {
        "user": parsed.username or "",
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "database": (parsed.path or "").lstrip("/"),
    }


def _table_exists(cur, schema: str, table_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        (schema, table_name),
    )
    return cur.fetchone() is not None


def _column_exists(cur, schema: str, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        (schema, table_name, column_name),
    )
    return cur.fetchone() is not None


def _index_exists(cur, schema: str, table_name: str, index_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = %s AND index_name = %s
        LIMIT 1
        """,
        (schema, table_name, index_name),
    )
    return cur.fetchone() is not None


def _ensure_table_channel_accounts(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_accounts (
            channel_account_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            admin_id INT NULL,
            channel VARCHAR(30) NOT NULL,
            provider VARCHAR(30) NOT NULL,
            account_label VARCHAR(120) NULL,
            sender_identity VARCHAR(191) NOT NULL,
            webhook_path_key VARCHAR(120) NULL,
            webhook_secret_enc TEXT NULL,
            credential_json_enc LONGTEXT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            is_primary TINYINT(1) NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_channel_provider_sender (channel, provider, sender_identity),
            UNIQUE KEY uq_channel_webhook_key (webhook_path_key),
            KEY idx_channel_accounts_admin (admin_id),
            KEY idx_channel_accounts_status (status)
        ) ENGINE=InnoDB
        """
    )


def _ensure_table_doctor_channel_bindings(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS doctor_channel_bindings (
            binding_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            doctor_id INT NOT NULL,
            clinic_id INT NULL,
            channel_account_id BIGINT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            is_primary TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_doctor_clinic_account (doctor_id, clinic_id, channel_account_id),
            KEY idx_dcb_doctor (doctor_id),
            KEY idx_dcb_clinic (clinic_id),
            KEY idx_dcb_channel_account (channel_account_id),
            KEY idx_dcb_primary (doctor_id, clinic_id, is_primary, status)
        ) ENGINE=InnoDB
        """
    )


def _ensure_table_route_cache_versions(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS route_cache_versions (
            entity VARCHAR(64) PRIMARY KEY,
            version BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """
    )
    cur.execute(
        """
        INSERT INTO route_cache_versions(entity, version)
        VALUES ('channel_routing', 1)
        ON DUPLICATE KEY UPDATE entity = entity
        """
    )


def _ensure_trigger(cur, trigger_name: str, trigger_sql: str) -> None:
    cur.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    cur.execute(trigger_sql)


def _ensure_column(cur, schema: str, table_name: str, column_name: str, definition: str) -> None:
    if not _table_exists(cur, schema, table_name):
        print(f"[WARN] Table missing, skipping column add: {table_name}.{column_name}")
        return
    if _column_exists(cur, schema, table_name, column_name):
        print(f"[OK] Column exists: {table_name}.{column_name}")
        return
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    print(f"[ADD] Column added: {table_name}.{column_name}")


def _ensure_index(cur, schema: str, table_name: str, index_name: str, index_sql: str) -> None:
    if not _table_exists(cur, schema, table_name):
        print(f"[WARN] Table missing, skipping index add: {index_name} on {table_name}")
        return
    if _index_exists(cur, schema, table_name, index_name):
        print(f"[OK] Index exists: {index_name}")
        return
    cur.execute(index_sql)
    print(f"[ADD] Index added: {index_name}")


def run() -> None:
    load_dotenv(".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set in environment/.env")

    cfg = _parse_mysql_url(database_url)
    schema = str(cfg["database"])
    if not schema:
        raise RuntimeError("DATABASE_URL is missing database name")

    conn = mysql.connector.connect(
        user=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=schema,
    )
    cur = conn.cursor()
    try:
        print(f"[INFO] Connected to {cfg['host']}:{cfg['port']} / {schema}")

        _ensure_table_channel_accounts(cur)
        _ensure_table_doctor_channel_bindings(cur)
        _ensure_table_route_cache_versions(cur)

        # Existing table extensions
        _ensure_column(cur, schema, "conversation_sessions", "channel_account_id", "BIGINT NULL")
        _ensure_column(cur, schema, "conversation_sessions", "source_channel", "VARCHAR(30) NULL")

        _ensure_column(cur, schema, "inbound_turn_queue", "channel_account_id", "BIGINT NULL")
        _ensure_column(cur, schema, "inbound_turn_queue", "source_channel", "VARCHAR(30) NULL")

        _ensure_column(cur, schema, "inbound_message_sids", "channel_account_id", "BIGINT NULL")
        _ensure_column(cur, schema, "inbound_message_sids", "source_channel", "VARCHAR(30) NULL")

        _ensure_column(cur, schema, "message_delivery_status", "channel_account_id", "BIGINT NULL")
        _ensure_column(cur, schema, "message_delivery_status", "doctor_id", "INT NULL")
        _ensure_column(cur, schema, "message_delivery_status", "admin_id", "INT NULL")

        _ensure_column(cur, schema, "appointment_notification_log", "channel_account_id", "BIGINT NULL")
        _ensure_column(cur, schema, "appointment_notification_log", "doctor_id", "INT NULL")

        # Indexes for new columns
        _ensure_index(
            cur,
            schema,
            "conversation_sessions",
            "idx_conv_sessions_channel_account",
            "CREATE INDEX idx_conv_sessions_channel_account ON conversation_sessions(channel_account_id)",
        )
        _ensure_index(
            cur,
            schema,
            "inbound_turn_queue",
            "idx_inbound_turn_channel_account",
            "CREATE INDEX idx_inbound_turn_channel_account ON inbound_turn_queue(channel_account_id)",
        )
        _ensure_index(
            cur,
            schema,
            "inbound_message_sids",
            "idx_inbound_sids_channel_account",
            "CREATE INDEX idx_inbound_sids_channel_account ON inbound_message_sids(channel_account_id)",
        )
        _ensure_index(
            cur,
            schema,
            "message_delivery_status",
            "idx_delivery_status_channel_account",
            "CREATE INDEX idx_delivery_status_channel_account ON message_delivery_status(channel_account_id)",
        )
        _ensure_index(
            cur,
            schema,
            "message_delivery_status",
            "idx_delivery_status_doctor",
            "CREATE INDEX idx_delivery_status_doctor ON message_delivery_status(doctor_id)",
        )
        _ensure_index(
            cur,
            schema,
            "message_delivery_status",
            "idx_delivery_status_admin",
            "CREATE INDEX idx_delivery_status_admin ON message_delivery_status(admin_id)",
        )
        _ensure_index(
            cur,
            schema,
            "appointment_notification_log",
            "idx_appt_notif_channel_account",
            "CREATE INDEX idx_appt_notif_channel_account ON appointment_notification_log(channel_account_id)",
        )
        _ensure_index(
            cur,
            schema,
            "appointment_notification_log",
            "idx_appt_notif_doctor",
            "CREATE INDEX idx_appt_notif_doctor ON appointment_notification_log(doctor_id)",
        )
        _ensure_index(
            cur,
            schema,
            "channel_accounts",
            "idx_channel_accounts_channel_status",
            "CREATE INDEX idx_channel_accounts_channel_status ON channel_accounts(channel, status)",
        )
        _ensure_index(
            cur,
            schema,
            "channel_accounts",
            "idx_channel_accounts_channel_webhook_status",
            "CREATE INDEX idx_channel_accounts_channel_webhook_status ON channel_accounts(channel, webhook_path_key, status)",
        )
        _ensure_index(
            cur,
            schema,
            "channel_accounts",
            "idx_channel_accounts_channel_sender_status",
            "CREATE INDEX idx_channel_accounts_channel_sender_status ON channel_accounts(channel, sender_identity, status)",
        )
        _ensure_index(
            cur,
            schema,
            "doctor_channel_bindings",
            "idx_dcb_account_status_primary",
            "CREATE INDEX idx_dcb_account_status_primary ON doctor_channel_bindings(channel_account_id, status, is_primary, binding_id)",
        )
        _ensure_index(
            cur,
            schema,
            "doctor_channel_bindings",
            "idx_dcb_doctor_status_primary",
            "CREATE INDEX idx_dcb_doctor_status_primary ON doctor_channel_bindings(doctor_id, status, is_primary, binding_id)",
        )

        # Route cache version triggers: any binding/account change bumps version.
        _ensure_trigger(
            cur,
            "trg_ca_route_ver_ai",
            """
            CREATE TRIGGER trg_ca_route_ver_ai
            AFTER INSERT ON channel_accounts
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )
        _ensure_trigger(
            cur,
            "trg_ca_route_ver_au",
            """
            CREATE TRIGGER trg_ca_route_ver_au
            AFTER UPDATE ON channel_accounts
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )
        _ensure_trigger(
            cur,
            "trg_ca_route_ver_ad",
            """
            CREATE TRIGGER trg_ca_route_ver_ad
            AFTER DELETE ON channel_accounts
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )
        _ensure_trigger(
            cur,
            "trg_dcb_route_ver_ai",
            """
            CREATE TRIGGER trg_dcb_route_ver_ai
            AFTER INSERT ON doctor_channel_bindings
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )
        _ensure_trigger(
            cur,
            "trg_dcb_route_ver_au",
            """
            CREATE TRIGGER trg_dcb_route_ver_au
            AFTER UPDATE ON doctor_channel_bindings
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )
        _ensure_trigger(
            cur,
            "trg_dcb_route_ver_ad",
            """
            CREATE TRIGGER trg_dcb_route_ver_ad
            AFTER DELETE ON doctor_channel_bindings
            FOR EACH ROW
            BEGIN
                INSERT INTO route_cache_versions(entity, version)
                VALUES ('channel_routing', 2)
                ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
            END
            """,
        )

        conn.commit()
        print("[DONE] Multi-channel additive migration completed successfully.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()
