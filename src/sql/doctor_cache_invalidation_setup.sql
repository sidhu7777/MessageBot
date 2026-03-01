-- Run this once on each target database after schema deployment.
-- It creates the invalidation queue table and (re)creates triggers for:
-- doctors, doctor_clinic_schedule, clinics, appointment.
--
-- Usage:
--   SOURCE src/sql/doctor_cache_invalidation_setup.sql;
--   CALL setup_doctor_cache_invalidation();

DROP PROCEDURE IF EXISTS setup_doctor_cache_invalidation;
DELIMITER $$
CREATE PROCEDURE setup_doctor_cache_invalidation()
BEGIN
    DECLARE has_doctors INT DEFAULT 0;
    DECLARE has_dcs INT DEFAULT 0;
    DECLARE has_clinics INT DEFAULT 0;
    DECLARE has_appointment INT DEFAULT 0;

    CREATE TABLE IF NOT EXISTS doctor_cache_invalidation_queue (
        queue_id BIGINT NOT NULL AUTO_INCREMENT,
        entity_type VARCHAR(20) NOT NULL,
        doctor_id INT NULL,
        clinic_id INT NULL,
        admin_id INT NULL,
        slot_date DATE NULL,
        slot_time VARCHAR(5) NULL,
        old_doctor_id INT NULL,
        old_clinic_id INT NULL,
        old_admin_id INT NULL,
        old_slot_date DATE NULL,
        old_slot_time VARCHAR(5) NULL,
        old_status VARCHAR(20) NULL,
        new_status VARCHAR(20) NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        lock_owner VARCHAR(80) NULL,
        locked_at DATETIME NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (queue_id),
        KEY idx_dcq_poll (status, queue_id),
        KEY idx_dcq_doc (doctor_id),
        KEY idx_dcq_clinic (clinic_id)
    ) ENGINE=InnoDB;

    SELECT COUNT(*) INTO has_doctors
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doctors';

    SELECT COUNT(*) INTO has_dcs
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doctor_clinic_schedule';

    SELECT COUNT(*) INTO has_clinics
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clinics';

    SELECT COUNT(*) INTO has_appointment
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'appointment';

    IF has_doctors > 0 THEN
        SET @sql = 'DROP TRIGGER IF EXISTS trg_doctors_cache_inv_ai'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_doctors_cache_inv_au'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_doctors_cache_inv_ad'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_doctors_cache_inv_ai
            AFTER INSERT ON doctors
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
            VALUES (''DOCTOR'', NEW.doctor_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_doctors_cache_inv_au
            AFTER UPDATE ON doctors
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
            VALUES (''DOCTOR'', NEW.doctor_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_doctors_cache_inv_ad
            AFTER DELETE ON doctors
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, admin_id)
            VALUES (''DOCTOR'', OLD.doctor_id, OLD.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
    END IF;

    IF has_dcs > 0 THEN
        SET @sql = 'DROP TRIGGER IF EXISTS trg_dcs_cache_inv_ai'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_dcs_cache_inv_au'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_dcs_cache_inv_ad'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_dcs_cache_inv_ai
            AFTER INSERT ON doctor_clinic_schedule
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
            VALUES (''SCHEDULE'', NEW.doctor_id, NEW.clinic_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_dcs_cache_inv_au
            AFTER UPDATE ON doctor_clinic_schedule
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
            VALUES (''SCHEDULE'', NEW.doctor_id, NEW.clinic_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_dcs_cache_inv_ad
            AFTER DELETE ON doctor_clinic_schedule
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, doctor_id, clinic_id, admin_id)
            VALUES (''SCHEDULE'', OLD.doctor_id, OLD.clinic_id, OLD.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
    END IF;

    IF has_clinics > 0 THEN
        SET @sql = 'DROP TRIGGER IF EXISTS trg_clinics_cache_inv_ai'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_clinics_cache_inv_au'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_clinics_cache_inv_ad'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_clinics_cache_inv_ai
            AFTER INSERT ON clinics
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
            VALUES (''CLINIC'', NEW.clinic_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_clinics_cache_inv_au
            AFTER UPDATE ON clinics
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
            VALUES (''CLINIC'', NEW.clinic_id, NEW.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_clinics_cache_inv_ad
            AFTER DELETE ON clinics
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(entity_type, clinic_id, admin_id)
            VALUES (''CLINIC'', OLD.clinic_id, OLD.admin_id)
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
    END IF;

    IF has_appointment > 0 THEN
        SET @sql = 'DROP TRIGGER IF EXISTS trg_appointment_cache_inv_ai'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_appointment_cache_inv_au'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
        SET @sql = 'DROP TRIGGER IF EXISTS trg_appointment_cache_inv_ad'; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_appointment_cache_inv_ai
            AFTER INSERT ON appointment
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(
                entity_type, doctor_id, clinic_id, admin_id, slot_date, slot_time, new_status
            )
            VALUES (
                ''APPOINTMENT'', NEW.doctor_id, NEW.clinic_id, NEW.admin_id,
                NEW.appointment_date, TIME_FORMAT(NEW.start_time, ''%H:%i''), NEW.status
            )
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_appointment_cache_inv_au
            AFTER UPDATE ON appointment
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(
                entity_type,
                doctor_id, clinic_id, admin_id, slot_date, slot_time, new_status,
                old_doctor_id, old_clinic_id, old_admin_id, old_slot_date, old_slot_time, old_status
            )
            VALUES (
                ''APPOINTMENT'',
                NEW.doctor_id, NEW.clinic_id, NEW.admin_id, NEW.appointment_date, TIME_FORMAT(NEW.start_time, ''%H:%i''), NEW.status,
                OLD.doctor_id, OLD.clinic_id, OLD.admin_id, OLD.appointment_date, TIME_FORMAT(OLD.start_time, ''%H:%i''), OLD.status
            )
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

        SET @sql = '
            CREATE TRIGGER trg_appointment_cache_inv_ad
            AFTER DELETE ON appointment
            FOR EACH ROW
            INSERT INTO doctor_cache_invalidation_queue(
                entity_type,
                old_doctor_id, old_clinic_id, old_admin_id, old_slot_date, old_slot_time, old_status
            )
            VALUES (
                ''APPOINTMENT'',
                OLD.doctor_id, OLD.clinic_id, OLD.admin_id, OLD.appointment_date, TIME_FORMAT(OLD.start_time, ''%H:%i''), OLD.status
            )
        '; PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
    END IF;
END $$
DELIMITER ;
