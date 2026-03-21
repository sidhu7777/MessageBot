-- Multi-channel / multi-bot schema migration (safe, additive only)
-- This script is intentionally additive:
-- - Creates new tables only if missing
-- - Adds new columns/indexes only if missing
-- - Does not drop/rename existing objects

-- 1) Channel account registry (stores one channel identity/config per bot/account)
CREATE TABLE IF NOT EXISTS channel_accounts (
    channel_account_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    admin_id INT NULL,
    channel VARCHAR(30) NOT NULL,                 -- telegram / whatsapp
    provider VARCHAR(30) NOT NULL,                -- telegram / twilio / meta / infobip
    account_label VARCHAR(120) NULL,              -- human-friendly name
    sender_identity VARCHAR(191) NOT NULL,        -- e.g. telegram bot username, WA sender number, phone_number_id
    webhook_path_key VARCHAR(120) NULL,           -- optional: unique key used in webhook path
    webhook_secret_enc TEXT NULL,                 -- encrypted secret/token for webhook validation
    credential_json_enc LONGTEXT NULL,            -- encrypted provider credentials payload
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE / INACTIVE
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_channel_provider_sender (channel, provider, sender_identity),
    UNIQUE KEY uq_channel_webhook_key (webhook_path_key),
    KEY idx_channel_accounts_admin (admin_id),
    KEY idx_channel_accounts_status (status)
) ENGINE=InnoDB;

-- 2) Binding between doctor(+optional clinic) and channel account
CREATE TABLE IF NOT EXISTS doctor_channel_bindings (
    binding_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    doctor_id INT NOT NULL,
    clinic_id INT NULL,
    channel_account_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE / INACTIVE
    is_primary TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_doctor_clinic_account (doctor_id, clinic_id, channel_account_id),
    KEY idx_dcb_doctor (doctor_id),
    KEY idx_dcb_clinic (clinic_id),
    KEY idx_dcb_channel_account (channel_account_id),
    KEY idx_dcb_primary (doctor_id, clinic_id, is_primary, status)
) ENGINE=InnoDB;

-- 2b) Version table for route-cache invalidation
CREATE TABLE IF NOT EXISTS route_cache_versions (
    entity VARCHAR(64) PRIMARY KEY,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

INSERT INTO route_cache_versions(entity, version)
VALUES ('channel_routing', 1)
ON DUPLICATE KEY UPDATE entity = entity;

-- 3) Existing table extensions required for account-scoped routing/audit
-- Note: run conditionally (or use the python helper) when IF NOT EXISTS on ADD COLUMN is unsupported.

ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS channel_account_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS source_channel VARCHAR(30) NULL;

ALTER TABLE inbound_turn_queue
    ADD COLUMN IF NOT EXISTS channel_account_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS source_channel VARCHAR(30) NULL;

ALTER TABLE inbound_message_sids
    ADD COLUMN IF NOT EXISTS channel_account_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS source_channel VARCHAR(30) NULL;

ALTER TABLE message_delivery_status
    ADD COLUMN IF NOT EXISTS channel_account_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS doctor_id INT NULL,
    ADD COLUMN IF NOT EXISTS admin_id INT NULL;

ALTER TABLE appointment_notification_log
    ADD COLUMN IF NOT EXISTS channel_account_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS doctor_id INT NULL;

-- 4) Add indexes for new columns (safe if not already present in your MySQL version)
CREATE INDEX IF NOT EXISTS idx_conv_sessions_channel_account
    ON conversation_sessions(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_inbound_turn_channel_account
    ON inbound_turn_queue(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_inbound_sids_channel_account
    ON inbound_message_sids(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_delivery_status_channel_account
    ON message_delivery_status(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_delivery_status_doctor
    ON message_delivery_status(doctor_id);

CREATE INDEX IF NOT EXISTS idx_delivery_status_admin
    ON message_delivery_status(admin_id);

CREATE INDEX IF NOT EXISTS idx_appt_notif_channel_account
    ON appointment_notification_log(channel_account_id);

CREATE INDEX IF NOT EXISTS idx_appt_notif_doctor
    ON appointment_notification_log(doctor_id);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_channel_status
    ON channel_accounts(channel, status);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_channel_webhook_status
    ON channel_accounts(channel, webhook_path_key, status);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_channel_sender_status
    ON channel_accounts(channel, sender_identity, status);

CREATE INDEX IF NOT EXISTS idx_dcb_account_status_primary
    ON doctor_channel_bindings(channel_account_id, status, is_primary, binding_id);

CREATE INDEX IF NOT EXISTS idx_dcb_doctor_status_primary
    ON doctor_channel_bindings(doctor_id, status, is_primary, binding_id);

-- 5) Triggers to bump route-cache version whenever channel routing data changes
DROP TRIGGER IF EXISTS trg_ca_route_ver_ai;
DROP TRIGGER IF EXISTS trg_ca_route_ver_au;
DROP TRIGGER IF EXISTS trg_ca_route_ver_ad;
DROP TRIGGER IF EXISTS trg_dcb_route_ver_ai;
DROP TRIGGER IF EXISTS trg_dcb_route_ver_au;
DROP TRIGGER IF EXISTS trg_dcb_route_ver_ad;

DELIMITER $$
CREATE TRIGGER trg_ca_route_ver_ai
AFTER INSERT ON channel_accounts
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$

CREATE TRIGGER trg_ca_route_ver_au
AFTER UPDATE ON channel_accounts
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$

CREATE TRIGGER trg_ca_route_ver_ad
AFTER DELETE ON channel_accounts
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$

CREATE TRIGGER trg_dcb_route_ver_ai
AFTER INSERT ON doctor_channel_bindings
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$

CREATE TRIGGER trg_dcb_route_ver_au
AFTER UPDATE ON doctor_channel_bindings
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$

CREATE TRIGGER trg_dcb_route_ver_ad
AFTER DELETE ON doctor_channel_bindings
FOR EACH ROW
BEGIN
    INSERT INTO route_cache_versions(entity, version)
    VALUES ('channel_routing', 2)
    ON DUPLICATE KEY UPDATE version = version + 1, updated_at = CURRENT_TIMESTAMP;
END$$
DELIMITER ;
