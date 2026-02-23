DROP PROCEDURE IF EXISTS generate_slots_for_schedule;


DELIMITER $$

CREATE PROCEDURE generate_slots_for_schedule(
    IN p_schedule_id INT,
    IN p_days_ahead INT
)
BEGIN
    DECLARE v_start_date DATE;
    DECLARE v_end_date DATE;
    DECLARE v_date DATE;
    DECLARE v_day_of_week INT;
    DECLARE v_start_time TIME;
    DECLARE v_end_time TIME;
    DECLARE v_slot_duration INT;

    -- Read schedule
    SELECT day_of_week, start_time, end_time, effective_from, effective_to,slot_duration
    INTO v_day_of_week, v_start_time, v_end_time, v_start_date, v_end_date, v_slot_duration
    FROM doctor_clinic_schedule
    WHERE schedule_id = p_schedule_id;

    -- Limit to p_days_ahead
    SET v_end_date = LEAST(DATE_ADD(CURDATE(), INTERVAL p_days_ahead DAY), v_end_date);

    SET v_date = v_start_date;

    WHILE v_date <= v_end_date DO

        -- Check weekday match
        IF MOD(WEEKDAY(v_date) + 1, 7) = v_day_of_week THEN

            SET @v_time = v_start_time;

            WHILE @v_time < v_end_time DO

                INSERT IGNORE INTO slots
                (schedule_id, slot_date, slot_time)
                VALUES
                (p_schedule_id, v_date, @v_time);

                SET @v_time = ADDTIME(@v_time, SEC_TO_TIME(v_slot_duration*60));

            END WHILE;

        END IF;

        SET v_date = DATE_ADD(v_date, INTERVAL 1 DAY);

    END WHILE;

END$$

DELIMITER ;
