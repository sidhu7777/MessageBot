USE defaultdb;

DROP TRIGGER IF EXISTS trg_appointment_doctor_cancel_log;
DROP TRIGGER IF EXISTS trg_appointment_doctor_reschedule_log;

DELIMITER $$

CREATE TRIGGER trg_appointment_doctor_cancel_log
AFTER UPDATE ON appointment
FOR EACH ROW
BEGIN
IF NEW.status = 'CANCELLED'
AND (OLD.status IS NULL OR OLD.status <> 'CANCELLED')
AND UPPER(COALESCE(NEW.cancelled_by, '')) = 'DOCTOR'
THEN
INSERT INTO appointment_notification_log
(
appointment_id,
event_type,
channel,
destination,
status,
error_text,
meta_json,
admin_id,
sent_at
)
SELECT
NEW.appointment_id,
'CANCELLED',
CASE
WHEN TRIM(COALESCE(NEW.notify_telegram_chat_id, '')) <> '' THEN 'telegram'
ELSE 'auto'
END,
CASE
WHEN TRIM(COALESCE(NEW.notify_telegram_chat_id, '')) <> ''
THEN CONCAT('telegram:', TRIM(NEW.notify_telegram_chat_id))
ELSE NULL
END,
'PENDING',
NULL,
JSON_OBJECT(
'cancelled_by', NEW.cancelled_by,
'old_status', OLD.status,
'new_status', NEW.status
),
NEW.admin_id,
NULL
WHERE NOT EXISTS (
SELECT 1
FROM appointment_notification_log l
WHERE l.appointment_id = NEW.appointment_id
AND l.event_type = 'CANCELLED'
AND l.status IN ('PENDING','FAILED','PROCESSING')
AND l.dead_at IS NULL
);
END IF;
END$$

CREATE TRIGGER trg_appointment_doctor_reschedule_log
AFTER UPDATE ON appointment
FOR EACH ROW
BEGIN
IF UPPER(COALESCE(NEW.rescheduled_by, '')) = 'DOCTOR'
AND NEW.status IN ('BOOKED', 'PENDING', 'CONFIRMED')
AND (
NOT (OLD.appointment_date <=> NEW.appointment_date)
OR NOT (OLD.start_time <=> NEW.start_time)
OR NOT (OLD.end_time <=> NEW.end_time)
)
THEN
INSERT INTO appointment_notification_log
(
appointment_id,
event_type,
channel,
destination,
status,
error_text,
meta_json,
admin_id,
sent_at
)
SELECT
NEW.appointment_id,
'RESCHEDULED',
CASE
WHEN TRIM(COALESCE(NEW.notify_telegram_chat_id, '')) <> '' THEN 'telegram'
ELSE 'auto'
END,
CASE
WHEN TRIM(COALESCE(NEW.notify_telegram_chat_id, '')) <> ''
THEN CONCAT('telegram:', TRIM(NEW.notify_telegram_chat_id))
ELSE NULL
END,
'PENDING',
NULL,
JSON_OBJECT(
'rescheduled_by', NEW.rescheduled_by,
'old_date', OLD.appointment_date,
'old_start_time', OLD.start_time,
'old_end_time', OLD.end_time,
'new_date', NEW.appointment_date,
'new_start_time', NEW.start_time,
'new_end_time', NEW.end_time
),
NEW.admin_id,
NULL
WHERE NOT EXISTS (
SELECT 1
FROM appointment_notification_log l
WHERE l.appointment_id = NEW.appointment_id
AND l.event_type = 'RESCHEDULED'
AND l.status IN ('PENDING','FAILED','PROCESSING')
AND l.dead_at IS NULL
);
END IF;
END$$

DELIMITER ;

