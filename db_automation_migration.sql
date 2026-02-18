-- Automation migration for schedule-driven slot rebuilds.
-- Safe to run multiple times.

CREATE TABLE IF NOT EXISTS schedule_rebuild_queue (
    schedule_id INT NOT NULL,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (schedule_id)
) ENGINE=InnoDB;

DROP TRIGGER IF EXISTS trg_schedule_rebuild_after_update;
DROP TRIGGER IF EXISTS trg_schedule_rebuild_after_insert;

DELIMITER $$

CREATE TRIGGER trg_schedule_rebuild_after_update
AFTER UPDATE ON doctor_clinic_schedule
FOR EACH ROW
BEGIN
    IF (
        NEW.start_time <> OLD.start_time OR
        NEW.end_time <> OLD.end_time OR
        NEW.slot_duration <> OLD.slot_duration OR
        NEW.day_of_week <> OLD.day_of_week OR
        NEW.effective_from <> OLD.effective_from OR
        NEW.effective_to <> OLD.effective_to OR
        IFNULL(NEW.doctor_id, 0) <> IFNULL(OLD.doctor_id, 0) OR
        IFNULL(NEW.clinic_id, 0) <> IFNULL(OLD.clinic_id, 0)
    ) THEN
        INSERT INTO schedule_rebuild_queue(schedule_id)
        VALUES (NEW.schedule_id)
        ON DUPLICATE KEY UPDATE requested_at = CURRENT_TIMESTAMP;
    END IF;
END$$

CREATE TRIGGER trg_schedule_rebuild_after_insert
AFTER INSERT ON doctor_clinic_schedule
FOR EACH ROW
BEGIN
    INSERT INTO schedule_rebuild_queue(schedule_id)
    VALUES (NEW.schedule_id)
    ON DUPLICATE KEY UPDATE requested_at = CURRENT_TIMESTAMP;
END$$

DELIMITER ;

-- One-time cleanup for bad historic rows.
DELETE FROM slots WHERE schedule_id IS NULL;

-- Remove duplicate unreferenced rows first.
DELETE s
FROM slots s
JOIN (
    SELECT schedule_id, slot_date, slot_time, MIN(slot_id) AS keep_id
    FROM slots
    GROUP BY schedule_id, slot_date, slot_time
    HAVING COUNT(*) > 1
) d
    ON s.schedule_id <=> d.schedule_id
   AND s.slot_date = d.slot_date
   AND s.slot_time = d.slot_time
   AND s.slot_id <> d.keep_id
LEFT JOIN appointments a ON a.slot_id = s.slot_id
WHERE a.slot_id IS NULL;

-- Add uniqueness guard to prevent repeat-generation duplicates.
ALTER TABLE slots
ADD CONSTRAINT uq_slots_schedule_date_time UNIQUE (schedule_id, slot_date, slot_time);
