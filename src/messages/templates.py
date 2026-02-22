def get_message(response_language: str, key: str, **kwargs: object) -> str:
    en = {
        "greeting": "Hello, I am your medical appointment assistant. I can help with booking and doctor availability.",
        "welcome_known_patient": "Welcome to Dr. {doctor_name} clinic, {patient_name}. How can I help you today?",
        "welcome_new_patient": "Welcome to Dr. {doctor_name} clinic. How can I help you today?",
        "general_help": "I can help with appointment booking or doctor availability. Tell me what you need.",
        "intent_ack": "Sure, I can help you book an appointment.",
        "availability_intro": "Sure, I can help check doctor availability. Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow'). Doctor name is optional.",
        "availability_ask": "Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow') to check availability.",
        "availability_ask_doctor": "Doctor name is optional. Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow').",
        "availability_ask_date": "Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow') to check availability.",
        "availability_noted": (
            "Noted. You want availability for Dr. {availability_doctor} on {availability_date}.\n"
            "You can continue with booking now by saying 'book appointment'."
        ),
        "empty_input": "Please send a message so I can assist you.",
        "no_intent": (
            "To begin, please say 'I need to book an appointment'."
        ),
        "clarify_intent": (
            "Please choose one option:\n"
            "1. Book appointment\n"
            "2. Check doctor availability"
        ),
        "final_booking_check": "Do you want to book a medical appointment now? Reply YES or NO.",
        "non_scope_final": (
            "Sorry, I am a medical appointment assistant and can only help with booking or doctor availability.\n"
            "Send 'book appointment' anytime to start."
        ),
        "ask_name": "Please share the patient full name.",
        "ask_booking_for": (
            "Who is this appointment for?\n"
            "1. Self\n"
            "2. Another person"
        ),
        "invalid_booking_for": "Please reply with 1 or 2.",
        "booking_for_self_ack": "Noted. Booking for self.",
        "booking_for_other_ack": "Noted. Booking for another person.",
        "go_back_hint": "0. Go back",
        "existing_booking_found": (
            "You already have a booked appointment:\n"
            "Reference ID: {appointment_id}\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Choose one option:\n"
            "1. Keep existing appointment\n"
            "2. Cancel appointment\n"
            "3. Reschedule (clinic/date/time)\n"
            "4. Book for another person"
        ),
        "existing_booking_choice_invalid": "Please reply with 1, 2, 3, or 4.",
        "existing_booking_choice_again": "Please choose again:\n1. Keep existing appointment\n2. Cancel appointment\n3. Reschedule\n4. Book for another person",
        "existing_booking_pick_header": "Please choose which booking you want to modify:",
        "existing_booking_pick_invalid": "Please choose a valid booking option number.",
        "max_active_bookings_reached": "For this number, maximum 2 active bookings are allowed. Please cancel/reschedule an existing booking first.",
        "existing_booking_keep": "Okay. Your existing appointment is kept as is.",
        "existing_booking_cancel_only_done": "Done. Your appointment has been cancelled.",
        "existing_booking_reschedule_start": "Okay. Let's reschedule this appointment. Previous clinic: {clinic_name}. You can choose same or another clinic.",
        "confirm_reschedule_summary": (
            "Please confirm reschedule:\n"
            "Clinic: {clinic_name}\n"
            "Old slot: {old_date} {old_time}\n"
            "New slot: {new_date} {new_time}\n"
            "Reply YES to confirm or NO to go back."
        ),
        "confirm_reschedule_prompt": "Please reply YES to confirm reschedule or NO to go back.",
        "reschedule_confirmed": (
            "Appointment rescheduled successfully.\n"
            "*Booking Number:* {appointment_id}\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "reschedule_failed": "Reschedule failed because the selected slot is not available now. Please try another date/time.",
        "existing_booking_cancel_failed": "I could not cancel the existing appointment right now. Please try again later.",
        "invalid_name": "Please provide a valid name. Example: Vineeth Raja Banala",
        "name_ack": "Thank you, {name}.",
        "ask_appointment_mode": (
            "Please choose appointment type:\n"
            "1. Online appointment\n"
            "2. Walk-in appointment\n"
            "Reply with 1 or 2."
        ),
        "invalid_appointment_mode": "Please reply with 1 or 2.",
        "appointment_mode_ack": "Noted. Appointment type: {appointment_mode}.",
        "ask_patient_type": (
            "Is the patient old or new?\n"
            "1. New\n"
            "2. Old\n"
            "Reply with 1 or 2."
        ),
        "invalid_patient_type": "Please reply with 1 or 2.",
        "patient_type_ack": "Noted. Patient type: {patient_type}.",
        "ask_age": "Please share patient age.",
        "invalid_age": "Please share a valid age between 1 and 120.",
        "age_ack": "Age noted: {age}.",
        "ask_gender": (
            "Please share patient gender:\n"
            "1. Male\n"
            "2. Female\n"
            "3. Other\n"
            "Reply with 1, 2, or 3."
        ),
        "invalid_gender": "Please reply with 1, 2, or 3.",
        "gender_ack": "Gender noted: {gender}.",
        "ask_phone": (
            "Is the contact number same as this WhatsApp number?\n"
            "Reply YES or NO.\n"
            "If NO, please share a 10-digit number."
        ),
        "ask_phone_telegram": "Please share a valid 10-digit contact number.",
        "invalid_phone_same_missing": "I could not read the WhatsApp number. Please share a 10-digit contact number.",
        "invalid_phone": "Please share a valid 10-digit contact number.",
        "phone_ack": "Contact number noted: {phone_number}.",
        "ask_clinic": (
            "Please choose clinic:\n"
            "1. City Care Clinic | MG Road, Hyderabad | Slots today: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | Slots today: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | Slots today: 4"
        ),
        "ask_clinic_header": "Please choose clinic:",
        "invalid_clinic": "Please reply with a valid option number, or type clinic name.",
        "no_clinic_available": "No clinics are available for booking right now. Please try again later.",
        "clinic_ack": "Clinic noted: {clinic_name}, {clinic_address}.",
        "ask_reason": (
            "Select reason (you can choose multiple like 1,3):\n"
            "1. Fever\n"
            "2. Headache\n"
            "3. Stomach pain\n"
            "4. Cold\n"
            "5. Other (type reason)\n"
            "Reply with 1, 2, 3, 4, or 5."
        ),
        "ask_reason_other": "Please type your reason.",
        "invalid_reason_option": "Please choose valid reason option(s), or type reason text.",
        "invalid_reason": "Please share the appointment reason in a few words.",
        "reason_ack": "Reason noted.",
        "ask_symptoms": "Please share the symptoms.",
        "invalid_symptoms": "Please share symptoms in a few words.",
        "symptoms_ack": "Symptoms noted.",
        "ask_date": "Please share preferred appointment date (YYYY-MM-DD or 'tomorrow').",
        "ask_date_options": (
            "Please choose appointment date:\n"
            "1. {date_1}\n"
            "2. {date_2}"
        ),
        "ask_date_manual": "Please choose only 1, 2, or 3.",
        "invalid_date": "Invalid date. Please send a future date in YYYY-MM-DD format.",
        "no_date_available": "No available dates for this clinic right now. Please choose another clinic or try later.",
        "date_ack": "Date noted: {appointment_date}.",
        "ask_time": "Please share preferred time (e.g., 10 am or 14:30).",
        "ask_time_hour_options": "Which hour is nearest for you to book?",
        "invalid_time_hour": "Please reply with a valid option number, or type an exact time.",
        "ask_time_nearest_slots": "Okay, for around {preferred_hour}, please choose an exact slot:",
        "ask_time_slots": (
            "Please choose a time slot:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "Reply with 1, 2, or 3.\n"
            "Or type another preferred time."
        ),
        "time_not_available": "Requested time {requested_time} is not available.",
        "no_time_available": "No available time slots for this date. Please choose another date.",
        "time_ack": "Time noted: {appointment_time}.",
        "invalid_time": "Invalid time format. Example: 10 am or 14:30",
        "confirm_summary": (
            "Please confirm your appointment details:\n"
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Reply YES to confirm or NO to change details."
        ),
        "confirm_prompt": "Please reply YES to confirm or NO to change details.",
        "ask_change_field": (
            "No problem. Which detail do you want to change?\n"
            "1. Name\n"
            "2. Contact number\n"
            "3. Clinic\n"
            "4. Date\n"
            "5. Time\n"
            "Reply with 1, 2, 3, 4, 5, or 6."
        ),
        "invalid_change_field": "Please choose a valid detail number (1-6).",
        "change_ack": (
            "No problem. Let's update that detail."
        ),
        "confirmed": (
            "Appointment request confirmed.\n"
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Send 'new appointment' for another booking."
        ),
        "reply_with_numbers": "Reply with {numbers}.",
        "not_confirmed": "No problem. Restarting booking flow. Please share the patient full name.",
        "completed_hint": "This appointment flow is complete. Send 'new appointment' to start another.",
        "db_save_ok": "Appointment booked successfully.\n*Booking Number:* {appointment_id}",
        "db_save_failed": "Booking confirmation received, but database save is pending manual follow-up.",
        "ended": "Understood. I have ended the process. Send 'book appointment' whenever you want to start again.",
        "cancelled_hint": "Process is ended. Send 'book appointment' to start a new booking.",
        "restart": "Restarting the appointment flow.",
    }

    hi = {
        "greeting": "à¤¨à¤®à¤¸à¥à¤¤à¥‡, à¤®à¥ˆà¤‚ à¤†à¤ªà¤•à¤¾ à¤®à¥‡à¤¡à¤¿à¤•à¤² à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤…à¤¸à¤¿à¤¸à¥à¤Ÿà¥‡à¤‚à¤Ÿ à¤¹à¥‚à¤à¥¤ à¤®à¥ˆà¤‚ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤”à¤° à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤®à¥‡à¤‚ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¤¤à¤¾ à¤¹à¥‚à¤à¥¤",
        "general_help": "à¤®à¥ˆà¤‚ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤¯à¤¾ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤®à¥‡à¤‚ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¤¤à¤¾ à¤¹à¥‚à¤à¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤…à¤ªà¤¨à¥€ à¤†à¤µà¤¶à¥à¤¯à¤•à¤¤à¤¾ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "intent_ack": "à¤ à¥€à¤• à¤¹à¥ˆ, à¤®à¥ˆà¤‚ à¤†à¤ªà¤•à¥€ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¬à¥à¤• à¤•à¤°à¤¨à¥‡ à¤®à¥‡à¤‚ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¤¤à¤¾ à¤¹à¥‚à¤à¥¤",
        "availability_intro": "à¤ à¥€à¤• à¤¹à¥ˆ, à¤®à¥ˆà¤‚ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤•à¥€ à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤¦à¥‡à¤–à¤¨à¥‡ à¤®à¥‡à¤‚ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¤¤à¤¾ à¤¹à¥‚à¤à¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤•à¤¾ à¤¨à¤¾à¤® à¤”à¤° à¤ªà¤¸à¤‚à¤¦à¥€à¤¦à¤¾ à¤¤à¤¾à¤°à¥€à¤– à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "availability_ask": "à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤¦à¥‡à¤–à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤•à¤¾ à¤¨à¤¾à¤® à¤”à¤° à¤¤à¤¾à¤°à¥€à¤– à¤­à¥‡à¤œà¥‡à¤‚ (YYYY-MM-DD à¤¯à¤¾ 'tomorrow').",
        "availability_ask_doctor": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤¦à¥‡à¤–à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤•à¤¾ à¤¨à¤¾à¤® à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "availability_ask_date": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤¦à¥‡à¤–à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤¤à¤¾à¤°à¥€à¤– à¤¬à¤¤à¤¾à¤à¤‚ (YYYY-MM-DD à¤¯à¤¾ 'tomorrow').",
        "availability_noted": (
            "à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤†à¤ª Dr. {availability_doctor} à¤•à¥‡ à¤²à¤¿à¤ {availability_date} à¤•à¥€ à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤ªà¥‚à¤› à¤°à¤¹à¥‡ à¤¹à¥ˆà¤‚à¥¤\n"
            "à¤¬à¥à¤•à¤¿à¤‚à¤— à¤œà¤¾à¤°à¥€ à¤°à¤–à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ 'book appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤"
        ),
        "empty_input": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤‚à¤¦à¥‡à¤¶ à¤­à¥‡à¤œà¥‡à¤‚ à¤¤à¤¾à¤•à¤¿ à¤®à¥ˆà¤‚ à¤†à¤ªà¤•à¥€ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¥‚à¤à¥¤",
        "no_intent": (
            "à¤¶à¥à¤°à¥‚ à¤•à¤°à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤²à¤¿à¤–à¥‡à¤‚: 'I need to book an appointment'."
        ),
        "clarify_intent": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤à¤• à¤µà¤¿à¤•à¤²à¥à¤ª à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¬à¥à¤• à¤•à¤°à¥‡à¤‚\n"
            "2. à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤¦à¥‡à¤–à¥‡à¤‚"
        ),
        "final_booking_check": "à¤•à¥à¤¯à¤¾ à¤†à¤ª à¤…à¤­à¥€ à¤®à¥‡à¤¡à¤¿à¤•à¤² à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¬à¥à¤• à¤•à¤°à¤¨à¤¾ à¤šà¤¾à¤¹à¤¤à¥‡ à¤¹à¥ˆà¤‚? à¤•à¥ƒà¤ªà¤¯à¤¾ YES à¤¯à¤¾ NO à¤®à¥‡à¤‚ à¤œà¤µà¤¾à¤¬ à¤¦à¥‡à¤‚à¥¤",
        "non_scope_final": (
            "à¤®à¤¾à¤«à¤¼ à¤•à¥€à¤œà¤¿à¤, à¤®à¥ˆà¤‚ à¤®à¥‡à¤¡à¤¿à¤•à¤² à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤…à¤¸à¤¿à¤¸à¥à¤Ÿà¥‡à¤‚à¤Ÿ à¤¹à¥‚à¤ à¤”à¤° à¤•à¥‡à¤µà¤² à¤¬à¥à¤•à¤¿à¤‚à¤— à¤¯à¤¾ à¤¡à¥‰à¤•à¥à¤Ÿà¤° à¤‰à¤ªà¤²à¤¬à¥à¤§à¤¤à¤¾ à¤®à¥‡à¤‚ à¤®à¤¦à¤¦ à¤•à¤° à¤¸à¤•à¤¤à¤¾ à¤¹à¥‚à¤à¥¤\n"
            "à¤¶à¥à¤°à¥‚ à¤•à¤°à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤•à¤­à¥€ à¤­à¥€ 'book appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤"
        ),
        "ask_name": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥‚à¤°à¤¾ à¤¨à¤¾à¤® à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "existing_booking_found": (
            "à¤†à¤ªà¤•à¥€ à¤à¤• à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤ªà¤¹à¤²à¥‡ à¤¸à¥‡ à¤¬à¥à¤• à¤¹à¥ˆ:\n"
            "à¤°à¥‡à¤«à¤°à¥‡à¤‚à¤¸ à¤†à¤ˆà¤¡à¥€: {appointment_id}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}\n"
            "à¤¤à¤¾à¤°à¥€à¤–: {appointment_date}\n"
            "à¤¸à¤®à¤¯: {appointment_time}\n"
            "à¤à¤• à¤µà¤¿à¤•à¤²à¥à¤ª à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. à¤ªà¥à¤°à¤¾à¤¨à¥€ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤°à¤–à¥‡à¤‚\n"
            "2. à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ cancel à¤•à¤°à¥‡à¤‚\n"
            "3. Reschedule à¤•à¤°à¥‡à¤‚ (clinic/date/time)"
        ),
        "existing_booking_choice_invalid": "à¤•à¥ƒà¤ªà¤¯à¤¾ 1, 2, à¤¯à¤¾ 3 à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "existing_booking_choice_again": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤«à¤¿à¤° à¤¸à¥‡ à¤šà¥à¤¨à¥‡à¤‚:\n1. à¤ªà¥à¤°à¤¾à¤¨à¥€ à¤°à¤–à¥‡à¤‚\n2. Cancel à¤•à¤°à¥‡à¤‚\n3. Reschedule à¤•à¤°à¥‡à¤‚",
        "existing_booking_keep": "à¤ à¥€à¤• à¤¹à¥ˆà¥¤ à¤†à¤ªà¤•à¥€ à¤®à¥Œà¤œà¥‚à¤¦à¤¾ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤µà¥ˆà¤¸à¥€ à¤¹à¥€ à¤°à¤¹à¥‡à¤—à¥€à¥¤",
        "existing_booking_cancel_only_done": "à¤ à¥€à¤• à¤¹à¥ˆà¥¤ à¤†à¤ªà¤•à¥€ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ cancel à¤•à¤° à¤¦à¥€ à¤—à¤ˆ à¤¹à¥ˆà¥¤",
        "existing_booking_reschedule_start": "à¤ à¥€à¤• à¤¹à¥ˆà¥¤ à¤†à¤ªà¤•à¥€ à¤ªà¥à¤°à¤¾à¤¨à¥€ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}à¥¤ à¤†à¤ª à¤µà¤¹à¥€ à¤¯à¤¾ à¤¦à¥‚à¤¸à¤°à¥€ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤šà¥à¤¨à¤•à¤° reschedule à¤•à¤° à¤¸à¤•à¤¤à¥‡ à¤¹à¥ˆà¤‚à¥¤",
        "confirm_reschedule_summary": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ reschedule à¤•à¥€ à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¤°à¥‡à¤‚:\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}\n"
            "à¤ªà¥à¤°à¤¾à¤¨à¤¾ à¤¸à¥à¤²à¥‰à¤Ÿ: {old_date} {old_time}\n"
            "à¤¨à¤¯à¤¾ à¤¸à¥à¤²à¥‰à¤Ÿ: {new_date} {new_time}\n"
            "à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¥‡ à¤²à¤¿à¤ YES à¤¯à¤¾ à¤µà¤¾à¤ªà¤¸ à¤œà¤¾à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ NO à¤­à¥‡à¤œà¥‡à¤‚à¥¤"
        ),
        "confirm_reschedule_prompt": "à¤•à¥ƒà¤ªà¤¯à¤¾ reschedule à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¥‡ à¤²à¤¿à¤ YES à¤¯à¤¾ à¤µà¤¾à¤ªà¤¸ à¤œà¤¾à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ NO à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "reschedule_confirmed": (
            "à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¸à¤«à¤²à¤¤à¤¾à¤ªà¥‚à¤°à¥à¤µà¤• reschedule à¤¹à¥‹ à¤—à¤ˆà¥¤\n"
            "*Booking Number:* {appointment_id}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}\n"
            "à¤¤à¤¾à¤°à¥€à¤–: {appointment_date}\n"
            "à¤¸à¤®à¤¯: {appointment_time}"
        ),
        "reschedule_failed": "Reschedule à¤…à¤¸à¤«à¤² à¤°à¤¹à¤¾ à¤•à¥à¤¯à¥‹à¤‚à¤•à¤¿ à¤šà¥à¤¨à¤¾ à¤—à¤¯à¤¾ à¤¸à¥à¤²à¥‰à¤Ÿ à¤…à¤­à¥€ à¤‰à¤ªà¤²à¤¬à¥à¤§ à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¦à¥‚à¤¸à¤°à¥€ à¤¤à¤¾à¤°à¥€à¤–/à¤¸à¤®à¤¯ à¤šà¥à¤¨à¥‡à¤‚à¥¤",
        "existing_booking_cancel_failed": "à¤…à¤­à¥€ à¤ªà¥à¤°à¤¾à¤¨à¥€ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ cancel à¤¨à¤¹à¥€à¤‚ à¤¹à¥‹ à¤ªà¤¾à¤ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¬à¤¾à¤¦ à¤®à¥‡à¤‚ à¤«à¤¿à¤° à¤•à¥‹à¤¶à¤¿à¤¶ à¤•à¤°à¥‡à¤‚à¥¤",
        "invalid_name": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤¹à¥€ à¤¨à¤¾à¤® à¤¬à¤¤à¤¾à¤à¤‚à¥¤ à¤‰à¤¦à¤¾à¤¹à¤°à¤£: Vineeth Raja Banala",
        "name_ack": "à¤§à¤¨à¥à¤¯à¤µà¤¾à¤¦, {name}à¥¤",
        "ask_patient_type": "à¤®à¤°à¥€à¤œ à¤ªà¥à¤°à¤¾à¤¨à¤¾ à¤¹à¥ˆ à¤¯à¤¾ à¤¨à¤¯à¤¾?\n1. New\n2. Old",
        "invalid_patient_type": "à¤•à¥ƒà¤ªà¤¯à¤¾ 'old' à¤¯à¤¾ 'new' à¤®à¥‡à¤‚ à¤‰à¤¤à¥à¤¤à¤° à¤¦à¥‡à¤‚à¥¤",
        "patient_type_ack": "à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥à¤°à¤•à¤¾à¤°: {patient_type}à¥¤",
        "ask_age": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¥€ à¤†à¤¯à¥ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "invalid_age": "à¤•à¥ƒà¤ªà¤¯à¤¾ 1 à¤¸à¥‡ 120 à¤•à¥‡ à¤¬à¥€à¤š à¤¸à¤¹à¥€ à¤†à¤¯à¥ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "age_ack": "à¤†à¤¯à¥ à¤¨à¥‹à¤Ÿ à¤•à¥€ à¤—à¤ˆ: {age}à¥¤",
        "ask_gender": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤œà¥‡à¤‚à¤¡à¤° à¤¬à¤¤à¤¾à¤à¤‚:\n1. Male\n2. Female\n3. Other",
        "invalid_gender": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤œà¥‡à¤‚à¤¡à¤° male, female, à¤¯à¤¾ other à¤®à¥‡à¤‚ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "gender_ack": "à¤œà¥‡à¤‚à¤¡à¤° à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾: {gender}à¥¤",
        "ask_phone": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤‚à¤ªà¤°à¥à¤• à¤¨à¤‚à¤¬à¤° à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¤°à¥‡à¤‚:\n"
            "1. à¤¯à¤¹à¥€ WhatsApp à¤¨à¤‚à¤¬à¤° à¤‰à¤ªà¤¯à¥‹à¤— à¤•à¤°à¥‡à¤‚\n"
            "2. à¤…à¤²à¤— à¤¨à¤‚à¤¬à¤° à¤­à¥‡à¤œà¥‡à¤‚ (10 à¤…à¤‚à¤•)"
        ),
        "ask_phone_telegram": "कृपया वैध 10 अंकों का संपर्क नंबर भेजें।",
        "invalid_phone_same_missing": "WhatsApp à¤¨à¤‚à¤¬à¤° à¤ªà¤¢à¤¼à¤¾ à¤¨à¤¹à¥€à¤‚ à¤œà¤¾ à¤¸à¤•à¤¾à¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ 10 à¤…à¤‚à¤•à¥‹à¤‚ à¤•à¤¾ à¤¨à¤‚à¤¬à¤° à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "invalid_phone": "à¤•à¥ƒà¤ªà¤¯à¤¾ 10 à¤…à¤‚à¤•à¥‹à¤‚ à¤•à¤¾ à¤¸à¤¹à¥€ à¤¸à¤‚à¤ªà¤°à¥à¤• à¤¨à¤‚à¤¬à¤° à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "phone_ack": "à¤¸à¤‚à¤ªà¤°à¥à¤• à¤¨à¤‚à¤¬à¤° à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾: {phone_number}à¥¤",
        "ask_clinic": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. City Care Clinic | MG Road, Hyderabad | à¤†à¤œ à¤¸à¥à¤²à¥‰à¤Ÿ: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | à¤†à¤œ à¤¸à¥à¤²à¥‰à¤Ÿ: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | à¤†à¤œ à¤¸à¥à¤²à¥‰à¤Ÿ: 4"
        ),
        "ask_clinic_header": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤šà¥à¤¨à¥‡à¤‚:",
        "invalid_clinic": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• 1, 2 à¤¯à¤¾ 3 à¤šà¥à¤¨à¥‡à¤‚à¥¤",
        "no_clinic_available": "à¤«à¤¿à¤²à¤¹à¤¾à¤² à¤¬à¥à¤•à¤¿à¤‚à¤— à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥‹à¤ˆ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤‰à¤ªà¤²à¤¬à¥à¤§ à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¬à¤¾à¤¦ à¤®à¥‡à¤‚ à¤«à¤¿à¤° à¤•à¥‹à¤¶à¤¿à¤¶ à¤•à¤°à¥‡à¤‚à¥¤",
        "clinic_ack": "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾: {clinic_name}, {clinic_address}à¥¤",
        "ask_reason": (
            "à¤•à¤¾à¤°à¤£ à¤šà¥à¤¨à¥‡à¤‚ (à¤à¤• à¤¸à¥‡ à¤…à¤§à¤¿à¤• à¤šà¥à¤¨ à¤¸à¤•à¤¤à¥‡ à¤¹à¥ˆà¤‚, à¤œà¥ˆà¤¸à¥‡ 1,3):\n"
            "1. Fever\n"
            "2. Headache\n"
            "3. Stomach pain\n"
            "4. Cold\n"
            "5. Other (à¤•à¤¾à¤°à¤£ à¤Ÿà¤¾à¤‡à¤ª à¤•à¤°à¥‡à¤‚)"
        ),
        "ask_reason_other": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¤¾à¤°à¤£ à¤Ÿà¤¾à¤‡à¤ª à¤•à¤°à¥‡à¤‚à¥¤",
        "invalid_reason_option": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤¹à¥€ à¤•à¤¾à¤°à¤£ à¤µà¤¿à¤•à¤²à¥à¤ª à¤šà¥à¤¨à¥‡à¤‚, à¤¯à¤¾ à¤•à¤¾à¤°à¤£ à¤Ÿà¥‡à¤•à¥à¤¸à¥à¤Ÿ à¤®à¥‡à¤‚ à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "invalid_reason": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¤¾à¤°à¤£ à¤¥à¥‹à¤¡à¤¼à¥‡ à¤¸à¥à¤ªà¤·à¥à¤Ÿ à¤°à¥‚à¤ª à¤®à¥‡à¤‚ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "reason_ack": "à¤•à¤¾à¤°à¤£ à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤",
        "ask_symptoms": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤²à¤•à¥à¤·à¤£ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "invalid_symptoms": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤²à¤•à¥à¤·à¤£ à¤¥à¥‹à¤¡à¤¼à¥‡ à¤¸à¥à¤ªà¤·à¥à¤Ÿ à¤°à¥‚à¤ª à¤®à¥‡à¤‚ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "symptoms_ack": "à¤²à¤•à¥à¤·à¤£ à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤ à¤—à¤à¥¤",
        "ask_date": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤ªà¤¸à¤‚à¤¦à¥€à¤¦à¤¾ à¤¤à¤¾à¤°à¥€à¤– à¤­à¥‡à¤œà¥‡à¤‚ (YYYY-MM-DD à¤¯à¤¾ 'tomorrow').",
        "ask_date_options": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¤à¤¾à¤°à¥€à¤– à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. {date_1}\n"
            "2. {date_2}\n"
            "3. Other date"
        ),
        "ask_date_manual": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¤à¤¾à¤°à¥€à¤– YYYY-MM-DD à¤«à¥‰à¤°à¥à¤®à¥‡à¤Ÿ à¤®à¥‡à¤‚ à¤Ÿà¤¾à¤‡à¤ª à¤•à¤°à¥‡à¤‚à¥¤",
        "invalid_date": "à¤¤à¤¾à¤°à¥€à¤– à¤…à¤®à¤¾à¤¨à¥à¤¯ à¤¹à¥ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤­à¤µà¤¿à¤·à¥à¤¯ à¤•à¥€ à¤¤à¤¾à¤°à¥€à¤– YYYY-MM-DD à¤«à¤¼à¥‰à¤°à¥à¤®à¥‡à¤Ÿ à¤®à¥‡à¤‚ à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "no_date_available": "à¤‡à¤¸ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤®à¥‡à¤‚ à¤«à¤¿à¤²à¤¹à¤¾à¤² à¤•à¥‹à¤ˆ à¤‰à¤ªà¤²à¤¬à¥à¤§ à¤¤à¤¾à¤°à¥€à¤– à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¦à¥‚à¤¸à¤°à¤¾ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤šà¥à¤¨à¥‡à¤‚ à¤¯à¤¾ à¤¬à¤¾à¤¦ à¤®à¥‡à¤‚ à¤•à¥‹à¤¶à¤¿à¤¶ à¤•à¤°à¥‡à¤‚à¥¤",
        "date_ack": "à¤¤à¤¾à¤°à¥€à¤– à¤¨à¥‹à¤Ÿ à¤•à¥€ à¤—à¤ˆ: {appointment_date}à¥¤",
        "ask_time": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤ªà¤¸à¤‚à¤¦à¥€à¤¦à¤¾ à¤¸à¤®à¤¯ à¤­à¥‡à¤œà¥‡à¤‚ (à¤œà¥ˆà¤¸à¥‡ 10 am à¤¯à¤¾ 14:30)à¥¤",
        "ask_time_slots": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¥à¤²à¥‰à¤Ÿ à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "à¤¯à¤¾ à¤¦à¥‚à¤¸à¤°à¤¾ à¤¸à¤®à¤¯ à¤Ÿà¤¾à¤‡à¤ª à¤•à¤°à¥‡à¤‚à¥¤"
        ),
        "time_not_available": "à¤®à¤¾à¤‚à¤—à¤¾ à¤—à¤¯à¤¾ à¤¸à¤®à¤¯ {requested_time} à¤‰à¤ªà¤²à¤¬à¥à¤§ à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤",
        "no_time_available": "à¤‡à¤¸ à¤¤à¤¾à¤°à¥€à¤– à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥‹à¤ˆ à¤‰à¤ªà¤²à¤¬à¥à¤§ à¤¸à¤®à¤¯ à¤¸à¥à¤²à¥‰à¤Ÿ à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¦à¥‚à¤¸à¤°à¥€ à¤¤à¤¾à¤°à¥€à¤– à¤šà¥à¤¨à¥‡à¤‚à¥¤",
        "time_ack": "à¤¸à¤®à¤¯ à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾: {appointment_time}à¥¤",
        "invalid_time": "à¤¸à¤®à¤¯ à¤•à¤¾ à¤«à¤¼à¥‰à¤°à¥à¤®à¥‡à¤Ÿ à¤…à¤®à¤¾à¤¨à¥à¤¯ à¤¹à¥ˆà¥¤ à¤‰à¤¦à¤¾à¤¹à¤°à¤£: 10 am à¤¯à¤¾ 14:30",
        "confirm_summary": (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤µà¤¿à¤µà¤°à¤£ à¤•à¥€ à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¤°à¥‡à¤‚:\n"
            "à¤¨à¤¾à¤®: {patient_name}\n"
            "à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥à¤°à¤•à¤¾à¤°: {patient_type}\n"
            "à¤†à¤¯à¥: {age}\n"
            "à¤œà¥‡à¤‚à¤¡à¤°: {gender}\n"
            "à¤¸à¤‚à¤ªà¤°à¥à¤•: {phone_number}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤ªà¤¤à¤¾: {clinic_address}\n"
            "à¤•à¤¾à¤°à¤£: {reason}\n"
            "à¤¤à¤¾à¤°à¥€à¤–: {appointment_date}\n"
            "à¤¸à¤®à¤¯: {appointment_time}\n"
            "à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¥‡ à¤²à¤¿à¤ YES à¤”à¤° à¤µà¤¿à¤µà¤°à¤£ à¤¬à¤¦à¤²à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ NO à¤­à¥‡à¤œà¥‡à¤‚à¥¤"
        ),
        "confirm_prompt": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤•à¥‡ à¤²à¤¿à¤ YES à¤¯à¤¾ à¤µà¤¿à¤µà¤°à¤£ à¤¬à¤¦à¤²à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ NO à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "ask_change_field": (
            "à¤•à¥‹à¤ˆ à¤¬à¤¾à¤¤ à¤¨à¤¹à¥€à¤‚à¥¤ à¤†à¤ª à¤•à¥Œà¤¨-à¤¸à¤¾ à¤µà¤¿à¤µà¤°à¤£ à¤¬à¤¦à¤²à¤¨à¤¾ à¤šà¤¾à¤¹à¤¤à¥‡ à¤¹à¥ˆà¤‚?\n"
            "1. à¤¨à¤¾à¤®\n"
            "2. à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥à¤°à¤•à¤¾à¤°\n"
            "3. à¤†à¤¯à¥\n"
            "4. à¤œà¥‡à¤‚à¤¡à¤°\n"
            "5. à¤¸à¤‚à¤ªà¤°à¥à¤• à¤¨à¤‚à¤¬à¤°\n"
            "6. à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•\n"
            "7. à¤¤à¤¾à¤°à¥€à¤–\n"
            "8. à¤¸à¤®à¤¯\n"
            "9. à¤•à¤¾à¤°à¤£\n"
            "10. à¤²à¤•à¥à¤·à¤£"
        ),
        "invalid_change_field": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤¹à¥€ à¤µà¤¿à¤•à¤²à¥à¤ª à¤šà¥à¤¨à¥‡à¤‚ (1-10), à¤¯à¤¾ à¤«à¤¼à¥€à¤²à¥à¤¡ à¤•à¤¾ à¤¨à¤¾à¤® à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "change_ack": (
            "à¤ à¥€à¤• à¤¹à¥ˆà¥¤ à¤¹à¤® à¤µà¤¹ à¤µà¤¿à¤µà¤°à¤£ à¤…à¤ªà¤¡à¥‡à¤Ÿ à¤•à¤°à¤¤à¥‡ à¤¹à¥ˆà¤‚à¥¤"
        ),
        "confirmed": (
            "à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤…à¤¨à¥à¤°à¥‹à¤§ à¤ªà¥à¤·à¥à¤Ÿà¤¿ à¤¹à¥‹ à¤—à¤¯à¤¾ à¤¹à¥ˆà¥¤\n"
            "à¤¨à¤¾à¤®: {patient_name}\n"
            "à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥à¤°à¤•à¤¾à¤°: {patient_type}\n"
            "à¤†à¤¯à¥: {age}\n"
            "à¤œà¥‡à¤‚à¤¡à¤°: {gender}\n"
            "à¤¸à¤‚à¤ªà¤°à¥à¤•: {phone_number}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤•: {clinic_name}\n"
            "à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤ªà¤¤à¤¾: {clinic_address}\n"
            "à¤•à¤¾à¤°à¤£: {reason}\n"
            "à¤¤à¤¾à¤°à¥€à¤–: {appointment_date}\n"
            "à¤¸à¤®à¤¯: {appointment_time}\n"
            "à¤¨à¤ˆ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤•à¥‡ à¤²à¤¿à¤ 'new appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤"
        ),
        "not_confirmed": "à¤•à¥‹à¤ˆ à¤¬à¤¾à¤¤ à¤¨à¤¹à¥€à¤‚à¥¤ à¤ªà¥à¤°à¤•à¥à¤°à¤¿à¤¯à¤¾ à¤¦à¥‹à¤¬à¤¾à¤°à¤¾ à¤¶à¥à¤°à¥‚ à¤•à¤° à¤°à¤¹à¤¾ à¤¹à¥‚à¤à¥¤ à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥‚à¤°à¤¾ à¤¨à¤¾à¤® à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
        "completed_hint": "à¤¯à¤¹ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤ªà¥‚à¤°à¥€ à¤¹à¥‹ à¤šà¥à¤•à¥€ à¤¹à¥ˆà¥¤ à¤¨à¤ˆ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤•à¥‡ à¤²à¤¿à¤ 'new appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "db_save_ok": "à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤¸à¤«à¤²à¤¤à¤¾à¤ªà¥‚à¤°à¥à¤µà¤• à¤¬à¥à¤• à¤¹à¥‹ à¤—à¤ˆà¥¤\n*à¤¬à¥à¤•à¤¿à¤‚à¤— à¤¨à¤‚à¤¬à¤°:* {appointment_id}",
        "db_save_failed": "à¤¬à¥à¤•à¤¿à¤‚à¤— à¤•à¤¨à¥à¤«à¤°à¥à¤® à¤¹à¥à¤ˆ, à¤²à¥‡à¤•à¤¿à¤¨ à¤¡à¥‡à¤Ÿà¤¾à¤¬à¥‡à¤¸ à¤¸à¥‡à¤µ à¤²à¤‚à¤¬à¤¿à¤¤ à¤¹à¥ˆà¥¤ à¤®à¥ˆà¤¨à¥à¤…à¤² à¤«à¥‰à¤²à¥‹-à¤…à¤ª à¤†à¤µà¤¶à¥à¤¯à¤• à¤¹à¥ˆà¥¤",
        "ended": "à¤ à¥€à¤• à¤¹à¥ˆ, à¤ªà¥à¤°à¤•à¥à¤°à¤¿à¤¯à¤¾ à¤¸à¤®à¤¾à¤ªà¥à¤¤ à¤•à¤° à¤¦à¥€ à¤—à¤ˆ à¤¹à¥ˆà¥¤ à¤¦à¥‹à¤¬à¤¾à¤°à¤¾ à¤¶à¥à¤°à¥‚ à¤•à¤°à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ 'book appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "cancelled_hint": "à¤ªà¥à¤°à¤•à¥à¤°à¤¿à¤¯à¤¾ à¤¸à¤®à¤¾à¤ªà¥à¤¤ à¤¹à¥ˆà¥¤ à¤¨à¤ˆ à¤¬à¥à¤•à¤¿à¤‚à¤— à¤•à¥‡ à¤²à¤¿à¤ 'book appointment' à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "restart": "à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤ªà¥à¤°à¤•à¥à¤°à¤¿à¤¯à¤¾ à¤¦à¥‹à¤¬à¤¾à¤°à¤¾ à¤¶à¥à¤°à¥‚ à¤•à¥€ à¤œà¤¾ à¤°à¤¹à¥€ à¤¹à¥ˆà¥¤",
    }

    hinglish = {
        "greeting": "Hello, main aapka medical appointment assistant hoon. Main booking aur doctor availability mein help kar sakta hoon.",
        "ask_booking_for": (
            "Yeh appointment kiske liye hai?\n"
            "1. Self\n"
            "2. Another person"
        ),
        "invalid_booking_for": "Please 1 ya 2 mein reply kariye.",
        "booking_for_self_ack": "Noted. Booking for self.",
        "booking_for_other_ack": "Noted. Booking for another person.",
        "go_back_hint": "0. Go back",
        "welcome_known_patient": "Welcome to Dr. {doctor_name} clinic, {patient_name}. How can I help you today?",
        "welcome_new_patient": "Welcome to Dr. {doctor_name} clinic. How can I help you today?",
        "general_help": "Main appointment booking ya doctor availability mein help kar sakta hoon. Aapko kya chahiye?",
        "intent_ack": "Theek hai, main aapki appointment booking mein help kar sakta hoon.",
        "availability_intro": "Theek hai, main doctor availability check karne mein help kar sakta hoon. Preferred date share kariye (YYYY-MM-DD ya 'today'/'tomorrow'). Doctor name optional hai.",
        "availability_ask": "Availability check ke liye preferred date bhejiye (YYYY-MM-DD ya 'today'/'tomorrow').",
        "availability_ask_doctor": "Availability check ke liye doctor name share kariye.",
        "availability_ask_date": "Availability check ke liye preferred date bhejiye (YYYY-MM-DD ya 'today'/'tomorrow').",
        "availability_noted": (
            "Noted. Aap Dr. {availability_doctor} ki {availability_date} ki availability pooch rahe hain.\n"
            "Booking continue karne ke liye 'book appointment' bhejiye."
        ),
        "empty_input": "Please message bhejiye taaki main help kar sakoon.",
        "no_intent": "Start karne ke liye please likhiye: 'I need to book an appointment'.",
        "clarify_intent": (
            "Please ek option choose kariye:\n"
            "1. Book appointment\n"
            "2. Check doctor availability"
        ),
        "final_booking_check": "Kya aap abhi medical appointment book karna chahte hain? YES ya NO mein reply kariye.",
        "non_scope_final": (
            "Sorry, main medical appointment assistant hoon aur sirf booking ya doctor availability mein help kar sakta hoon.\n"
            "Start karne ke liye kabhi bhi 'book appointment' bhejiye."
        ),
        "ask_name": "Please patient ka full name share kariye.",
        "existing_booking_found": (
            "Aapka ek booked appointment already hai:\n"
            "Reference ID: {appointment_id}\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Please ek option choose kariye:\n"
            "1. Keep existing appointment\n"
            "2. Cancel appointment\n"
            "3. Reschedule (clinic/date/time)\n"
            "4. Another person ke liye book karein"
        ),
        "existing_booking_choice_invalid": "Please 1, 2, 3, ya 4 reply kariye.",
        "existing_booking_choice_again": "Please fir se choose kariye:\n1. Keep existing\n2. Cancel\n3. Reschedule\n4. Another person ke liye book karein",
        "existing_booking_pick_header": "Please kaunsa booking modify karna hai choose kariye:",
        "existing_booking_pick_invalid": "Please valid booking option number choose kariye.",
        "max_active_bookings_reached": "Is number par maximum 2 active bookings allowed hain. Pehle existing booking cancel ya reschedule kariye.",
        "existing_booking_keep": "Theek hai. Aapka existing appointment same rahega.",
        "existing_booking_cancel_only_done": "Done. Aapka appointment cancel ho gaya.",
        "existing_booking_reschedule_start": "Theek hai. Previous clinic: {clinic_name}. Aap same ya doosra clinic choose karke reschedule kar sakte hain.",
        "confirm_reschedule_summary": (
            "Please reschedule confirm kariye:\n"
            "Clinic: {clinic_name}\n"
            "Old slot: {old_date} {old_time}\n"
            "New slot: {new_date} {new_time}\n"
            "Confirm ke liye YES ya back ke liye NO bhejiye."
        ),
        "confirm_reschedule_prompt": "Please reschedule confirm ke liye YES ya back ke liye NO bhejiye.",
        "reschedule_confirmed": (
            "Appointment successfully reschedule ho gaya.\n"
            "*Booking Number:* {appointment_id}\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "reschedule_failed": "Reschedule fail hua kyunki selected slot ab available nahi hai. Please doosri date/time try kariye.",
        "existing_booking_cancel_failed": "Existing appointment abhi cancel nahi ho paaya. Please baad mein try kariye.",
        "invalid_name": "Please valid name batayiye. Example: Vineeth Raja Banala",
        "name_ack": "Thank you, {name}.",
        "ask_appointment_mode": (
            "Please appointment type choose kariye:\n"
            "1. Online appointment\n"
            "2. Walk-in appointment\n"
            "Please 1 ya 2 number mein reply kariye."
        ),
        "invalid_appointment_mode": "Please 1 ya 2 number mein reply kariye.",
        "appointment_mode_ack": "Noted. Appointment type: {appointment_mode}.",
        "ask_patient_type": "Patient old hai ya new?\n1. New\n2. Old",
        "invalid_patient_type": "Please 'old' ya 'new' mein reply kariye.",
        "patient_type_ack": "Noted. Patient type: {patient_type}.",
        "ask_age": "Please patient age share kariye.",
        "invalid_age": "Please 1 se 120 ke beech valid age batayiye.",
        "age_ack": "Age noted: {age}.",
        "ask_gender": "Please patient gender share kariye:\n1. Male\n2. Female\n3. Other",
        "invalid_gender": "Please gender male, female, ya other mein reply kariye.",
        "gender_ack": "Gender noted: {gender}.",
        "ask_phone": (
            "Kya contact number yehi WhatsApp number hai?\n"
            "Please YES ya NO mein reply kariye.\n"
            "Agar NO, to 10-digit number share kariye."
        ),
        "ask_phone_telegram": "Please valid 10-digit contact number share kariye.",
        "invalid_phone_same_missing": "WhatsApp number read nahi hua. Please 10-digit contact number share kariye.",
        "invalid_phone": "Please valid 10-digit contact number bhejiye.",
        "phone_ack": "Contact number noted: {phone_number}.",
        "ask_clinic": (
            "Please clinic choose kariye:\n"
            "1. City Care Clinic | MG Road, Hyderabad | Aaj ke slots: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | Aaj ke slots: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | Aaj ke slots: 4"
        ),
        "ask_clinic_header": "Please clinic choose kariye:",
        "invalid_clinic": "Please clinic 1, 2, ya 3 choose kariye.",
        "no_clinic_available": "Abhi booking ke liye koi clinic available nahi hai. Please thodi der baad try kariye.",
        "clinic_ack": "Clinic noted: {clinic_name}, {clinic_address}.",
        "ask_reason": (
            "Reason select kariye (multiple possible, e.g. 1,3):\n"
            "1. Fever\n"
            "2. Headache\n"
            "3. Stomach pain\n"
            "4. Cold\n"
            "5. Other (reason type kariye)"
        ),
        "ask_reason_other": "Please apna reason type kariye.",
        "invalid_reason_option": "Please valid reason option choose kariye, ya reason text type kariye.",
        "invalid_reason": "Please appointment reason thoda clearly batayiye.",
        "reason_ack": "Reason noted.",
        "ask_symptoms": "Please symptoms share kariye.",
        "invalid_symptoms": "Please symptoms thoda clearly batayiye.",
        "symptoms_ack": "Symptoms noted.",
        "ask_date": "Please preferred appointment date bhejiye (YYYY-MM-DD ya 'tomorrow').",
        "ask_date_options": (
            "Please appointment date choose kariye:\n"
            "1. {date_1}\n"
            "2. {date_2}\n"
            "3. Other date"
        ),
        "ask_date_manual": "Please date YYYY-MM-DD format mein type kariye.",
        "invalid_date": "Invalid date. Please future date YYYY-MM-DD format mein bhejiye.",
        "no_date_available": "Is clinic ke liye abhi koi date available nahi hai. Please doosra clinic choose kariye ya baad mein try kariye.",
        "date_ack": "Date noted: {appointment_date}.",
        "ask_time": "Please preferred time share kariye (e.g., 10 am ya 14:30).",
        "ask_time_slots": (
            "Please time slot choose kariye:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "Ya apna preferred time type kariye."
        ),
        "time_not_available": "Requested time {requested_time} available nahi hai.",
        "no_time_available": "Is date ke liye koi time slot available nahi hai. Please doosri date choose kariye.",
        "time_ack": "Time noted: {appointment_time}.",
        "invalid_time": "Invalid time format. Example: 10 am ya 14:30",
        "confirm_summary": (
            "Please appointment details confirm kariye:\n"
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Confirm ke liye YES ya details change karne ke liye NO bhejiye."
        ),
        "confirm_prompt": "Please confirm ke liye YES ya details change karne ke liye NO bhejiye.",
        "ask_change_field": (
            "No problem. Kaunsa detail change karna hai?\n"
            "1. Name\n"
            "2. Contact number\n"
            "3. Clinic\n"
            "4. Date\n"
            "5. Time"
        ),
        "invalid_change_field": "Please valid option choose kariye (1-5).",
        "change_ack": "No problem. Chaliye woh detail update karte hain.",
        "confirmed": (
            "Appointment request confirm ho gaya.\n"
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Nayi booking ke liye 'new appointment' bhejiye."
        ),
        "not_confirmed": "No problem. Booking flow restart kar raha hoon. Please patient ka full name share kariye.",
        "completed_hint": "Yeh appointment flow complete ho chuka hai. Nayi booking ke liye 'new appointment' bhejiye.",
        "db_save_ok": "Appointment successfully book ho gaya.\n*Booking Number:* {appointment_id}",
        "db_save_failed": "Booking confirm ho gayi, lekin database save pending hai. Manual follow-up zaroori hai.",
        "ended": "Theek hai, process end kar diya gaya hai. Dobara start karne ke liye 'book appointment' bhejiye.",
        "cancelled_hint": "Process ended hai. Nayi booking ke liye 'book appointment' bhejiye.",
        "restart": "Appointment flow restart kiya ja raha hai.",
    }

    if response_language == "hi":
        source = hi
    elif response_language == "hinglish":
        source = hinglish
    else:
        source = en
    if response_language == "hi" and key == "ask_appointment_mode":
        return (
            "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¨à¤¿à¤¯à¥à¤•à¥à¤¤à¤¿ à¤ªà¥à¤°à¤•à¤¾à¤° à¤šà¥à¤¨à¥‡à¤‚:\n"
            "1. à¤‘à¤¨à¤²à¤¾à¤‡à¤¨ à¤ªà¤°à¤¾à¤®à¤°à¥à¤¶\n"
            "2. à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• à¤®à¥‡à¤‚ à¤ªà¤°à¤¾à¤®à¤°à¥à¤¶\n"
            "à¤•à¥ƒà¤ªà¤¯à¤¾ 1 à¤¯à¤¾ 2 à¤®à¥‡à¤‚ à¤‰à¤¤à¥à¤¤à¤° à¤¦à¥‡à¤‚à¥¤"
        )
    if response_language == "hi" and key == "invalid_appointment_mode":
        return "à¤•à¥ƒà¤ªà¤¯à¤¾ 1 à¤¯à¤¾ 2 à¤®à¥‡à¤‚ à¤‰à¤¤à¥à¤¤à¤° à¤¦à¥‡à¤‚à¥¤"
    if response_language == "hi" and key == "appointment_mode_ack":
        return f"à¤¨à¥‹à¤Ÿ à¤•à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤ªà¥à¤°à¤•à¤¾à¤°: {kwargs.get('appointment_mode', '-')}à¥¤"
    if key == "change_ack":
        step = kwargs.get("step")
        if source is en:
            prompts = {
                "ASK_NAME": "Please share the patient full name.",
                "ASK_APPOINTMENT_MODE": "Please choose appointment type: 1. Online appointment 2. Walk-in appointment.",
                "ASK_PATIENT_TYPE": "Is the patient old or new?",
                "ASK_AGE": "Please share patient age.",
                "ASK_GENDER": "Please share patient gender: male, female, or other.",
                "ASK_PHONE": "Is this WhatsApp number the contact number? Reply YES or NO. If NO, share a 10-digit number.",
                "ASK_CLINIC": "Please choose clinic 1, 2, or 3.",
                "ASK_REASON": "What is the reason for the appointment?",
                "ASK_SYMPTOMS": "Please share the symptoms.",
                "ASK_DATE": "Please share preferred appointment date (YYYY-MM-DD or 'tomorrow').",
                "ASK_TIME": "Please share preferred time (e.g., 10 am or 14:30).",
            }
        elif source is hi:
            prompts = {
                "ASK_NAME": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤ªà¥‚à¤°à¤¾ à¤¨à¤¾à¤® à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
                "ASK_PATIENT_TYPE": "à¤®à¤°à¥€à¤œ à¤ªà¥à¤°à¤¾à¤¨à¤¾ à¤¹à¥ˆ à¤¯à¤¾ à¤¨à¤¯à¤¾?",
                "ASK_AGE": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¥€ à¤†à¤¯à¥ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
                "ASK_GENDER": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤®à¤°à¥€à¤œ à¤•à¤¾ à¤œà¥‡à¤‚à¤¡à¤° à¤¬à¤¤à¤¾à¤à¤‚: male, female, à¤¯à¤¾ otherà¥¤",
                "ASK_PHONE": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤‚à¤ªà¤°à¥à¤• à¤¨à¤‚à¤¬à¤° à¤­à¥‡à¤œà¥‡à¤‚ (10 à¤…à¤‚à¤•)à¥¤",
                "ASK_CLINIC": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤•à¥à¤²à¤¿à¤¨à¤¿à¤• 1, 2 à¤¯à¤¾ 3 à¤šà¥à¤¨à¥‡à¤‚à¥¤",
                "ASK_REASON": "à¤…à¤ªà¥‰à¤‡à¤‚à¤Ÿà¤®à¥‡à¤‚à¤Ÿ à¤•à¤¾ à¤•à¤¾à¤°à¤£ à¤•à¥à¤¯à¤¾ à¤¹à¥ˆ?",
                "ASK_SYMPTOMS": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤²à¤•à¥à¤·à¤£ à¤¬à¤¤à¤¾à¤à¤‚à¥¤",
                "ASK_DATE": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤ªà¤¸à¤‚à¤¦à¥€à¤¦à¤¾ à¤¤à¤¾à¤°à¥€à¤– à¤­à¥‡à¤œà¥‡à¤‚ (YYYY-MM-DD à¤¯à¤¾ 'tomorrow').",
                "ASK_TIME": "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤ªà¤¸à¤‚à¤¦à¥€à¤¦à¤¾ à¤¸à¤®à¤¯ à¤­à¥‡à¤œà¥‡à¤‚ (à¤œà¥ˆà¤¸à¥‡ 10 am à¤¯à¤¾ 14:30)à¥¤",
            }
        else:
            prompts = {
                "ASK_NAME": "Please patient ka full name share kariye.",
                "ASK_APPOINTMENT_MODE": "Please appointment type choose kariye: 1. Online appointment 2. Walk-in appointment.",
                "ASK_PATIENT_TYPE": "Patient old hai ya new?",
                "ASK_AGE": "Please patient age share kariye.",
                "ASK_GENDER": "Please patient gender share kariye: male, female, ya other.",
                "ASK_PHONE": "Kya yehi WhatsApp number contact number hai? YES ya NO reply kariye. Agar NO, to 10-digit number share kariye.",
                "ASK_CLINIC": "Please clinic 1, 2, ya 3 choose kariye.",
                "ASK_REASON": "Appointment ka reason kya hai?",
                "ASK_SYMPTOMS": "Please symptoms share kariye.",
                "ASK_DATE": "Please preferred appointment date bhejiye (YYYY-MM-DD ya 'tomorrow').",
                "ASK_TIME": "Please preferred time share kariye (e.g., 10 am ya 14:30).",
            }
        return source[key].format(**kwargs) + "\n" + prompts.get(step, "")
    template = source.get(key, en.get(key, key))
    return template.format(**kwargs)

