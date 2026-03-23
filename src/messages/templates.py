import re


def _normalize_menu_spacing(text: str) -> str:
    normalized = text.replace("\n\n\n", "\n\n")
    replacements = {
        '\n0. Go back': '\n\nPress "0" to go back.',
        '\nPress "0" to go back.': '\n\nPress "0" to go back.',
        '\n0. वापस जाएं': '\n\n"0" दबाकर वापस जाएं।',
        '\n"0" दबाकर वापस जाएं।': '\n\n"0" दबाकर वापस जाएं।',
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


def _emphasize_patient_id_line(text: str) -> str:
    emphasized = re.sub(
        r"\*Appointment ID:\*\s*([^\n]+)",
        r"*Appointment ID: \1*",
        text,
    )
    emphasized = re.sub(
        r"\*अपॉइंटमेंट आईडी:\*\s*([^\n]+)",
        r"*अपॉइंटमेंट आईडी: \1*",
        emphasized,
    )
    return emphasized


def get_message(response_language: str, key: str, **kwargs: object) -> str:
    en = {
        "greeting": "Hello, I am your medical appointment assistant. I can help with booking and doctor availability.",
        "welcome_known_patient": "Welcome to Dr. {doctor_name} clinic, {patient_name}. How can I help you today?",
        "welcome_new_patient": "Welcome to Dr. {doctor_name} clinic. How can I help you today?",
        "welcome_known_patient_booking": "Welcome to Dr. {doctor_name} clinic, {patient_name}.",
        "welcome_new_patient_booking": "Welcome to Dr. {doctor_name} clinic.",
        "language_selection_known_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic, {patient_name}.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है, {patient_name}।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "language_selection_new_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "invalid_language_selection": (
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "timeout_processing_delay": "We are facing a delay while processing your request. Please wait, we will respond shortly.",
        "intent_ack": "Sure, I can help you book an appointment.",
        "availability_intro": "Sure, I can help check doctor availability. Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow'). Doctor name is optional.",
        "availability_ask": "Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow') to check availability.",
        "availability_ask_doctor": "Doctor name is optional. Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow').",
        "availability_ask_date": "Please share preferred date (YYYY-MM-DD or 'today'/'tomorrow') to check availability.",
        "availability_noted": (
            "Noted. You want availability for Dr. {availability_doctor} on {availability_date}.\n"
            "You can continue with booking now by saying 'book appointment'."
        ),
        "availability_generic_noted": (
            "Noted. You want doctor availability on {availability_date}.\n"
            "Please share doctor name if you want doctor-specific availability.\n\n"
            "Press \"0\" to go back."
        ),
        "availability_result_available_header": "Doctor availability on {availability_date}:",
        "availability_result_next_header": "No slots available on {availability_date}.\nNext available dates:",
        "availability_result_none": 'No slots available on {availability_date}.\n\n1. Book appointment\nPress "0" to go back.',
        "availability_result_actions": '\n\n1. Book appointment\n0. Go back',
        "availability_result_actions_back": '\n\n1. Book appointment\nPress "0" to go back.',
        "empty_input": "Please send a message so I can assist you.",
        "abusive_language": "I can help only with appointment tasks. Please use respectful language. If you continue with rude language, your account will be blocked.",
        "abusive_language_final": "Your account has been blocked due to disrespectful language. Please contact the clinic directly for further assistance.",
        "no_intent": (
            "To begin, please say 'I need to book an appointment'."
        ),
        "clarify_intent": (
            "Please choose one option:\n"
            "1. Book appointment\n"
            "2. Check doctor availability\n"
            "0. Go back\n"
            "Reply with 1, 2, or 0."
        ),
        "menu_warning_today_unavailable": (
            "No slots are available today for Dr. {doctor_name}.\n"
            "The next available booking date is {next_available_date}.\n"
            "If you want to continue booking for the next available dates within the allowed booking window, please choose one option below."
        ),
        "menu_warning_no_clinic_window": (
            "No clinic is available for booking right now for Dr. {doctor_name}.\n"
            "Please try booking again on {retry_date}."
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
        "invalid_booking_for": "Please reply with 1, 2, or 0.",
        "booking_for_self_ack": "Noted. Booking for self.",
        "booking_for_other_ack": "Noted. Booking for another person.",
        "go_back_hint": "Press \"0\" to go back.",
        "existing_booking_found": (
            "You already have a booked appointment:\n"
            "Appointment ID: {appointment_id}\n"
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
        "max_active_bookings_actions": (
            "Choose one option:\n"
            "1. Cancel an existing appointment\n"
            "2. Reschedule an existing appointment\n"
            "Press \"0\" to go back."
        ),
        "max_active_bookings_invalid": "Please reply with 1, 2, or 0.",
        "existing_booking_keep": "Okay. Your existing appointment is kept as is.",
        "existing_booking_cancel_only_done": "Done. Your appointment has been cancelled.",
        "existing_booking_reschedule_start": "Okay. Let's reschedule this appointment. Previous clinic: {clinic_name}. You can choose same or another clinic.",
        "confirm_reschedule_summary": (
            "Please confirm reschedule:\n"
            "Clinic: {clinic_name}\n"
            "Old slot: {old_date} {old_time}\n"
            "New slot: {new_date} {new_time}\n"
            "1. Confirm\n"
            "2. Change details\n"
            "0. Go back\n"
            "Reply with 1, 2, or 0."
        ),
        "confirm_reschedule_prompt": "1. Confirm\n2. Change details\n\n0. Go back\nReply with 1, 2, or 0.",
        "reschedule_confirmed": (
            "Appointment rescheduled successfully.\n"
            "*Appointment ID: {appointment_id}*\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "reschedule_failed": "Reschedule failed because the selected slot is not available now. Please try another date/time.",
        "existing_booking_cancel_failed": "I could not cancel the existing appointment right now. Please try again later.",
        "invalid_name": "Please provide a valid name. Example: Vineeth Raja Banala",
        "name_ack": "Name noted: {name}.",
        "ask_appointment_mode": (
            "Please choose appointment type:\n"
            "1. Online appointment\n"
            "2. Walk-in appointment\n"
            "Reply with 1, 2, or 0."
        ),
        "invalid_appointment_mode": "Please reply with 1, 2, or 0.",
        "appointment_mode_ack": "Noted. Appointment type: {appointment_mode}.",
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
            "1. City Care Clinic, Address: MG Road, Hyderabad. Slots today: 7\n"
            "2. Sunrise Health Center, Address: KPHB, Hyderabad. Slots today: 5\n"
            "3. Green Valley Clinic, Address: Gachibowli, Hyderabad. Slots today: 4"
        ),
        "ask_clinic_header": "Please choose clinic:",
        "invalid_clinic": "Please reply with a valid option number, or type clinic name.",
        "no_clinic_available": "No clinics are available for booking right now. Please try again later.",
        "no_clinic_available_restart": "No clinics are available right now. Please try later. Send 'book appointment' to restart.",
        "clinic_ack": "Clinic noted: {clinic_name}, {clinic_address}.",
        "ask_date": "Please share preferred appointment date (YYYY-MM-DD or 'tomorrow').",
        "date_options_header": "Please choose appointment date:",
        "availability_date_options_header": "Please choose a date to check availability:",
        "date_label_today": "Today ({date})",
        "date_label_tomorrow": "Tomorrow ({date})",
        "ask_date_options": (
            "Please choose appointment date:\n"
            "1. {date_1}\n"
            "2. {date_2}"
        ),
        "ask_date_manual": "Please choose a valid option number, or press \"0\" to go back.",
        "invalid_date": "Invalid date. Please send a future date in YYYY-MM-DD format.",
        "no_date_available": "No available slots for {clinic_name}. Please choose another clinic.",
        "date_ack": "Date noted: {appointment_date}.",
        "ask_time": "Please share preferred time (e.g., 10 am or 14:30).",
        "ask_time_period_header": "Please choose preferred time period:",
        "time_period_morning": "Morning",
        "time_period_afternoon": "Afternoon",
        "time_period_evening": "Evening",
        "ask_time_hour_options": "Which hour is nearest for you to book?",
        "choose_slot_header": "Please choose a slot:",
        "invalid_time_hour": "Please reply with a valid option number, or type an exact time.",
        "ask_time_nearest_slots": "Okay, for around {preferred_hour}, please choose an exact slot:",
        "ask_time_slots": (
            "Please choose a time slot:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "Reply with 1, 2, 3, or 0.\n"
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
            "1. Confirm\n"
            "2. Change details\n"
            "0. Go back\n"
            "Reply with 1, 2, or 0."
        ),
        "confirm_prompt": "1. Confirm\n2. Change details\n\n0. Go back\nReply with 1, 2, or 0.",
        "ask_change_field": (
            "No problem. Which detail do you want to change?\n"
            "1. Name\n"
            "2. Contact number\n"
            "3. Clinic\n"
            "4. Date\n"
            "5. Time\n"
            "0. Go back\n"
            "Reply with 1, 2, 3, 4, 5, or 0."
        ),
        "invalid_change_field": "Please choose a valid detail number (1-5 or 0).",
        "change_ack": (
            "No problem. Let's update that detail."
        ),
        "confirmed": (
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "reply_with_numbers": "Reply with {numbers}.",
        "not_confirmed": "No problem. Restarting booking flow. Please share the patient full name.",
        "completed_hint": "This appointment flow is complete. Send 'book appointment' to start another.",
        "db_save_ok": "Appointment booked successfully.\n*Appointment ID: {appointment_id}*",
        "db_save_failed": "Booking confirmation received, but database save is pending manual follow-up.",
        "ended": "Understood. I have ended the process. Send 'book appointment' whenever you want to start again.",
        "cancelled_hint": "Process is ended. Send 'book appointment' to start a new booking.",
        "restart": "Restarting the appointment flow.",
    }

    hi = {
        "greeting": "नमस्ते, मैं आपका मेडिकल अपॉइंटमेंट असिस्टेंट हूँ। मैं बुकिंग और डॉक्टर उपलब्धता में मदद कर सकता हूँ।",
        "welcome_known_patient": "डॉ. {doctor_name} के क्लिनिक में आपका स्वागत है, {patient_name}। मैं आज आपकी किस प्रकार मदद कर सकता हूँ?",
        "welcome_new_patient": "डॉ. {doctor_name} के क्लिनिक में आपका स्वागत है। मैं आज आपकी किस प्रकार मदद कर सकता हूँ?",
        "welcome_known_patient_booking": "डॉ. {doctor_name} के क्लिनिक में आपका स्वागत है, {patient_name}।",
        "welcome_new_patient_booking": "डॉ. {doctor_name} के क्लिनिक में आपका स्वागत है।",
        "language_selection_known_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic, {patient_name}.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है, {patient_name}।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "language_selection_new_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "invalid_language_selection": (
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "timeout_processing_delay": "आपके अनुरोध को संसाधित करने में देरी हो रही है। कृपया थोड़ी देर प्रतीक्षा करें, हम शीघ्र उत्तर देंगे।",
        "intent_ack": "ठीक है, मैं आपकी अपॉइंटमेंट बुक करने में मदद कर सकता हूँ।",
        "availability_intro": "ठीक है, मैं डॉक्टर की उपलब्धता देखने में मदद कर सकता हूँ। कृपया डॉक्टर का नाम और पसंदीदा तारीख बताएं।",
        "availability_ask": "उपलब्धता देखने के लिए कृपया डॉक्टर का नाम और तारीख भेजें (YYYY-MM-DD या 'कल').",
        "availability_ask_doctor": "कृपया उपलब्धता देखने के लिए डॉक्टर का नाम बताएं।",
        "availability_ask_date": "कृपया उपलब्धता देखने के लिए तारीख बताएं (YYYY-MM-DD या 'कल').",
        "availability_noted": (
            "नोट किया गया। आप डॉ. {availability_doctor} के लिए {availability_date} की उपलब्धता पूछ रहे हैं।\n"
            "बुकिंग जारी रखने के लिए 'अपॉइंटमेंट बुक करें' भेजें।"
        ),
        "availability_generic_noted": (
            "नोट किया गया। आप {availability_date} के लिए डॉक्टर की उपलब्धता पूछ रहे हैं।\n"
            "यदि आप किसी विशेष डॉक्टर की उपलब्धता चाहते हैं, तो कृपया डॉक्टर का नाम बताएं।\n\n"
            "\"0\" दबाकर वापस जाएं।"
        ),
        "availability_result_available_header": "{availability_date} के लिए डॉक्टर की उपलब्धता:",
        "availability_result_next_header": "{availability_date} को कोई स्लॉट उपलब्ध नहीं है।\nअगली उपलब्ध तारीखें:",
        "availability_result_none": ' {availability_date} को कोई स्लॉट उपलब्ध नहीं है।\n\n1. अपॉइंटमेंट बुक करें\n"0" दबाकर वापस जाएं।',
        "availability_result_actions": '\n\n1. अपॉइंटमेंट बुक करें\n0. वापस जाएं',
        "availability_result_actions_back": '\n\n1. अपॉइंटमेंट बुक करें\n"0" दबाकर वापस जाएं।',
        "empty_input": "कृपया संदेश भेजें ताकि मैं आपकी मदद कर सकूँ।",
        "abusive_language": "मैं केवल अपॉइंटमेंट संबंधित मदद कर सकता हूँ। कृपया सम्मानजनक भाषा का प्रयोग करें। अगर आप फिर से असभ्य भाषा का प्रयोग करते हैं, तो आपका खाता अवरुद्ध कर दिया जाएगा।",
        "abusive_language_final": "आपका खाता असभ्य भाषा के कारण ब्लॉक कर दिया गया है। कृपया आगे की मदद के लिए सीधे क्लिनिक से संपर्क करें।",
        "no_intent": "शुरू करने के लिए कृपया लिखें: 'मुझे अपॉइंटमेंट बुक करनी है'।",
        "clarify_intent": (
            "कृपया एक विकल्प चुनें:\n"
            "1. अपॉइंटमेंट बुक करें\n"
            "2. डॉक्टर उपलब्धता जांचें\n"
            "\n"
            "\"0\" दबाकर वापस जाएं।\n"
            "कृपया 1, 2, या 0 में उत्तर दें।"
        ),
        "menu_warning_today_unavailable": (
            "डॉ. {doctor_name} के लिए आज कोई स्लॉट उपलब्ध नहीं है।\n"
            "अगली उपलब्ध बुकिंग तारीख {next_available_date} है।\n"
            "यदि आप अनुमति प्राप्त बुकिंग अवधि के भीतर अगली उपलब्ध तारीखों के लिए बुकिंग जारी रखना चाहते हैं, तो नीचे एक विकल्प चुनें।"
        ),
        "menu_warning_no_clinic_window": (
            "डॉ. {doctor_name} के लिए इस समय कोई क्लिनिक बुकिंग के लिए उपलब्ध नहीं है।\n"
            "कृपया {retry_date} को फिर से बुकिंग करने का प्रयास करें।"
        ),
        "ask_booking_for": (
            "यह अपॉइंटमेंट किसके लिए है?\n"
            "1. स्वयं के लिए\n"
            "2. किसी अन्य व्यक्ति के लिए"
        ),
        "invalid_booking_for": "कृपया 1, 2, या 0 में उत्तर दें।",
        "booking_for_self_ack": "नोट किया गया। स्वयं के लिए बुकिंग की जा रही है।",
        "booking_for_other_ack": "नोट किया गया। किसी अन्य व्यक्ति के लिए बुकिंग की जा रही है।",
        "go_back_hint": "\"0\" दबाकर वापस जाएं।",
        "existing_booking_found": (
            "आपकी एक बुक की हुई अपॉइंटमेंट पहले से मौजूद है:\n"
            "अपॉइंटमेंट आईडी: {appointment_id}\n"
            "क्लिनिक: {clinic_name}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}\n"
            "एक विकल्प चुनें:\n"
            "1. पुरानी अपॉइंटमेंट रखें\n"
            "2. अपॉइंटमेंट रद्द करें\n"
            "3. पुनर्निर्धारित करें (क्लिनिक/तारीख/समय)\n"
            "4. किसी और व्यक्ति के लिए बुक करें"
        ),
        "existing_booking_choice_invalid": "कृपया 1, 2, 3, या 4 भेजें।",
        "existing_booking_choice_again": "कृपया फिर से चुनें:\n1. पुरानी रखें\n2. रद्द करें\n3. पुनर्निर्धारित करें\n4. किसी और व्यक्ति के लिए बुक करें",
        "existing_booking_keep": "ठीक है। आपकी मौजूदा अपॉइंटमेंट वैसी ही रहेगी।",
        "existing_booking_cancel_only_done": "ठीक है। आपकी अपॉइंटमेंट रद्द कर दी गई है।",
        "existing_booking_reschedule_start": "ठीक है। आपकी पुरानी क्लिनिक: {clinic_name}। आप वही या दूसरी क्लिनिक चुनकर पुनर्निर्धारित कर सकते हैं।",
        "max_active_bookings_actions": (
            "एक विकल्प चुनें:\n"
            "1. किसी पुरानी अपॉइंटमेंट को रद्द करें\n"
            "2. किसी पुरानी अपॉइंटमेंट को पुनर्निर्धारित करें\n"
            "0. वापस जाएं"
        ),
        "max_active_bookings_invalid": "कृपया 1, 2, या 0 भेजें।",
        "confirm_reschedule_summary": (
            "कृपया पुनर्निर्धारण की पुष्टि करें:\n"
            "क्लिनिक: {clinic_name}\n"
            "पुराना स्लॉट: {old_date} {old_time}\n"
            "नया स्लॉट: {new_date} {new_time}\n"
            "1. पुष्टि करें\n"
            "2. विवरण बदलें\n"
            "0. वापस जाएं\n"
            "कृपया 1, 2, या 0 में उत्तर दें।"
        ),
        "confirm_reschedule_prompt": "1. पुष्टि करें\n2. विवरण बदलें\n0. वापस जाएं\nकृपया 1, 2, या 0 में उत्तर दें।",
        "reschedule_confirmed": (
            "अपॉइंटमेंट सफलतापूर्वक पुनर्निर्धारित हो गई।\n"
            "*अपॉइंटमेंट आईडी: {appointment_id}*\n"
            "क्लिनिक: {clinic_name}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}"
        ),
        "reschedule_failed": "पुनर्निर्धारण असफल रहा क्योंकि चुना गया स्लॉट अभी उपलब्ध नहीं है। कृपया दूसरी तारीख/समय चुनें।",
        "existing_booking_cancel_failed": "अभी पुरानी अपॉइंटमेंट रद्द नहीं हो पाई। कृपया बाद में फिर कोशिश करें।",
        "invalid_name": "कृपया सही नाम बताएं। उदाहरण: Vineeth Raja Banala",
        "name_ack": "नाम नोट किया गया: {name}।",
        "ask_phone": (
            "कृपया संपर्क नंबर की पुष्टि करें:\n"
            "1. यही व्हाट्सऐप नंबर उपयोग करें\n"
            "2. अलग नंबर भेजें (10 अंक)"
        ),
        "ask_phone_telegram": "कृपया वैध 10 अंकों का संपर्क नंबर भेजें।",
        "invalid_phone_same_missing": "व्हाट्सऐप नंबर पढ़ा नहीं जा सका। कृपया 10 अंकों का नंबर भेजें।",
        "invalid_phone": "कृपया 10 अंकों का सही संपर्क नंबर भेजें।",
        "phone_ack": "संपर्क नंबर नोट किया गया: {phone_number}।",
        "ask_clinic": (
            "कृपया क्लिनिक चुनें:\n"
            "1. City Care Clinic, पता: MG Road, Hyderabad। आज स्लॉट: 7\n"
            "2. Sunrise Health Center, पता: KPHB, Hyderabad। आज स्लॉट: 5\n"
            "3. Green Valley Clinic, पता: Gachibowli, Hyderabad। आज स्लॉट: 4"
        ),
        "ask_clinic_header": "कृपया क्लिनिक चुनें:",
        "invalid_clinic": "कृपया क्लिनिक 1, 2 या 3 चुनें।",
        "no_clinic_available": "फिलहाल बुकिंग के लिए कोई क्लिनिक उपलब्ध नहीं है। कृपया बाद में फिर कोशिश करें।",
        "no_clinic_available_restart": "फिलहाल कोई क्लिनिक उपलब्ध नहीं है। कृपया बाद में कोशिश करें। पुनः शुरू करने के लिए 'अपॉइंटमेंट बुक करें' भेजें।",
        "clinic_ack": "क्लिनिक नोट किया गया: {clinic_name}, {clinic_address}।",
        "ask_date": "कृपया पसंदीदा तारीख भेजें (YYYY-MM-DD या 'कल').",
        "date_options_header": "कृपया अपॉइंटमेंट की तारीख चुनें:",
        "availability_date_options_header": "कृपया उपलब्धता जांचने के लिए तारीख चुनें:",
        "date_label_today": "आज ({date})",
        "date_label_tomorrow": "कल ({date})",
        "ask_date_options": (
            "कृपया अपॉइंटमेंट तारीख चुनें:\n"
            "1. {date_1}\n"
            "2. {date_2}\n"
            "3. दूसरी तारीख"
        ),
        "ask_date_manual": "कृपया सही विकल्प संख्या चुनें, या वापस जाने के लिए 0 भेजें।",
        "invalid_date": "तारीख अमान्य है। कृपया भविष्य की तारीख YYYY-MM-DD फ़ॉर्मेट में भेजें।",
        "no_date_available": "{clinic_name} में फिलहाल कोई स्लॉट उपलब्ध नहीं है। कृपया दूसरा क्लिनिक चुनें।",
        "date_ack": "तारीख नोट की गई: {appointment_date}।",
        "ask_time": "कृपया पसंदीदा समय भेजें (जैसे 10 am या 14:30)।",
        "choose_slot_header": "कृपया एक स्लॉट चुनें:",
        "ask_time_slots": (
            "कृपया स्लॉट चुनें:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "या दूसरा समय टाइप करें।\n"
            "कृपया 1, 2, 3, या 0 में उत्तर दें।\n"
        ),
        "time_not_available": "माँगा गया समय {requested_time} उपलब्ध नहीं है।",
        "no_time_available": "इस तारीख के लिए कोई उपलब्ध समय स्लॉट नहीं है। कृपया दूसरी तारीख चुनें।",
        "time_ack": "समय नोट किया गया: {appointment_time}।",
        "invalid_time": "समय का फ़ॉर्मेट अमान्य है। उदाहरण: 10 am या 14:30",
        "confirm_summary": (
            "कृपया अपॉइंटमेंट विवरण की पुष्टि करें:\n"
            "नाम: {patient_name}\n"
            "संपर्क: {phone_number}\n"
            "क्लिनिक: {clinic_name}\n"
            "क्लिनिक पता: {clinic_address}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}\n"
            "1. पुष्टि करें\n"
            "2. विवरण बदलें\n"
            "0. वापस जाएं\n"
            "कृपया 1, 2, या 0 में उत्तर दें।"
        ),
        "confirm_prompt": "1. पुष्टि करें\n2. विवरण बदलें\n0. वापस जाएं\nकृपया 1, 2, या 0 में उत्तर दें।",
        "ask_change_field": (
            "कोई बात नहीं। आप कौन-सा विवरण बदलना चाहते हैं?\n"
            "1. नाम\n"
            "2. संपर्क नंबर\n"
            "3. क्लिनिक\n"
            "4. तारीख\n"
            "5. समय\n"
            "0. वापस जाएं\n"
            "कृपया 1, 2, 3, 4, 5, या 0 में उत्तर दें।"
        ),
        "invalid_change_field": "कृपया मान्य विकल्प चुनें (1-5 या 0)।",
        "change_ack": "ठीक है। हम वह विवरण अपडेट करते हैं।",
        "confirmed": (
            "नाम: {patient_name}\n"
            "संपर्क: {phone_number}\n"
            "क्लिनिक: {clinic_name}\n"
            "क्लिनिक पता: {clinic_address}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}"
        ),
        "reply_with_numbers": "कृपया {numbers} में उत्तर दें।",
        "not_confirmed": "कोई बात नहीं। प्रक्रिया दोबारा शुरू कर रहा हूँ। कृपया मरीज़ का पूरा नाम बताएं।",
        "completed_hint": "यह बुकिंग पूरी हो चुकी है। नई बुकिंग के लिए 'अपॉइंटमेंट बुक करें' भेजें।",
        "db_save_ok": "अपॉइंटमेंट सफलतापूर्वक बुक हो गई।\n*अपॉइंटमेंट आईडी: {appointment_id}*",
        "db_save_failed": "बुकिंग की पुष्टि हो गई, लेकिन डेटाबेस सेव लंबित है। मैनुअल फ़ॉलो-अप आवश्यक है।",
        "ended": "ठीक है, प्रक्रिया समाप्त कर दी गई है। दोबारा शुरू करने के लिए 'अपॉइंटमेंट बुक करें' भेजें।",
        "cancelled_hint": "प्रक्रिया समाप्त है। नई बुकिंग के लिए 'अपॉइंटमेंट बुक करें' भेजें।",
        "restart": "अपॉइंटमेंट प्रक्रिया दोबारा शुरू की जा रही है।",
    }
    hinglish = {
        "greeting": "Hello, main aapka medical appointment assistant hoon. Main booking aur doctor availability mein help kar sakta hoon.",
        "language_selection_known_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic, {patient_name}.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है, {patient_name}।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "language_selection_new_patient": (
            "Hello / नमस्ते\n\n"
            "Welcome to Dr. {doctor_name} clinic.\n"
            "डॉ. {doctor_name} क्लिनिक में आपका स्वागत है।\n\n"
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "invalid_language_selection": (
            "Please choose your language:\n"
            "कृपया अपनी भाषा चुनें:\n\n"
            "1. English\n"
            "2. हिंदी\n"
            "3. Hinglish"
        ),
        "timeout_processing_delay": "Processing mein delay ho raha hai. Please wait, hum jaldi response denge.",
        "ask_booking_for": (
            "Yeh appointment kiske liye hai?\n"
            "1. Self\n"
            "2. Another person"
        ),
        "invalid_booking_for": "Please 1, 2, ya 0 mein reply kariye.",
        "booking_for_self_ack": "Noted. Booking for self.",
        "booking_for_other_ack": "Noted. Booking for another person.",
        "go_back_hint": "Press \"0\" to go back.",
        "welcome_known_patient": "Welcome to Dr. {doctor_name} clinic, {patient_name}. How can I help you today?.",
        "welcome_new_patient": "Welcome to Dr. {doctor_name} clinic. How can I help you today?",
        "welcome_known_patient_booking": "Welcome to Dr. {doctor_name} clinic, {patient_name}.",
        "welcome_new_patient_booking": "Welcome to Dr. {doctor_name} clinic.",
        "intent_ack": "Theek hai, main aapki appointment booking mein help kar sakta hoon.",
        "availability_intro": "Theek hai, main doctor availability check karne mein help kar sakta hoon. Preferred date share kariye (YYYY-MM-DD ya 'today'/'tomorrow'). Doctor name optional hai.",
        "availability_ask": "Availability check ke liye preferred date bhejiye (YYYY-MM-DD ya 'today'/'tomorrow').",
        "availability_ask_doctor": "Availability check ke liye doctor name share kariye.",
        "availability_ask_date": "Availability check ke liye preferred date bhejiye (YYYY-MM-DD ya 'today'/'tomorrow').",
        "availability_noted": (
            "Noted. Aap Dr. {availability_doctor} ki {availability_date} ki availability pooch rahe hain.\n"
            "Booking continue karne ke liye 'book appointment' bhejiye."
        ),
        "availability_generic_noted": (
            "Noted. Aap {availability_date} ke liye doctor availability pooch rahe hain.\n"
            "Agar doctor-specific availability chahiye, to doctor name share kariye.\n\n"
            "Press \"0\" to go back."
        ),
        "availability_result_available_header": "Doctor availability on {availability_date}:",
        "availability_result_next_header": "{availability_date} par koi slot available nahi hai.\nNext available dates:",
        "availability_result_none": ' {availability_date} par koi slot available nahi hai.\n\n1. Appointment book kariye\nPress "0" to go back.',
        "availability_result_actions": '\n\n1. Appointment book kariye\n0. Go back',
        "availability_result_actions_back": '\n\n1. Appointment book kariye\nPress "0" to go back.',
        "empty_input": "Please message bhejiye taaki main help kar sakoon.",
        "abusive_language": "Main sirf appointment related help kar sakta hoon. Please respectful language use kariye. Agar aap fir se rude language use karenge, to aapka account block ho jayega.",
        "abusive_language_final": "Aapka account disrespectful language ke kaaran block kar diya gaya hai. Aage ki help ke liye please clinic ko directly contact kariye.",
        "no_intent": "Start karne ke liye please likhiye: 'I need to book an appointment'.",
        "clarify_intent": (
            "Please ek option choose kariye:\n"
            "1. Appointment book kariye\n"
            "2. Doctor availability check kariye\n"
            "0. Go back\n"
            "1, 2, ya 0 se reply kariye."
        ),
        "menu_warning_today_unavailable": (
            "Dr. {doctor_name} ke liye aaj koi slot available nahi hai.\n"
            "Next available booking date {next_available_date} hai.\n"
            "Agar aap allowed booking window ke andar next available dates ke liye booking continue karna chahte hain, to neeche ek option choose kariye."
        ),
        "menu_warning_no_clinic_window": (
            "Dr. {doctor_name} ke liye abhi koi clinic booking ke liye available nahi hai.\n"
            "Please {retry_date} ko phir se booking try kariye."
        ),
        "existing_booking_found": (
            "Aapki ek booked appointment pehle se maujood hai:\n"
            "Appointment ID: {appointment_id}\n"
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
        "max_active_bookings_actions": (
            "Ek option choose kariye:\n"
            "1. Existing appointment cancel kariye\n"
            "2. Existing appointment reschedule kariye\n"
            "Press \"0\" to go back."
        ),
        "max_active_bookings_invalid": "Please 1, 2, ya 0 reply kariye.",
        "existing_booking_keep": "Theek hai. Aapka existing appointment same rahega.",
        "existing_booking_cancel_only_done": "Done. Aapka appointment cancel ho gaya.",
        "existing_booking_reschedule_start": "Theek hai. Previous clinic: {clinic_name}. Aap same ya doosra clinic choose karke reschedule kar sakte hain.",
        "confirm_reschedule_summary": (
            "Please reschedule confirm kariye:\n"
            "Clinic: {clinic_name}\n"
            "Old slot: {old_date} {old_time}\n"
            "New slot: {new_date} {new_time}\n"
            "1. Confirm kariye\n"
            "2. Details badliye\n"
            "0. Go back\n"
            "Reply with 1, 2, or 0."
        ),
        "confirm_reschedule_prompt": "1. Confirm kariye\n2. Details badliye\n0. Go back\nReply with 1, 2, or 0.",
        "reschedule_confirmed": (
            "Appointment successfully reschedule ho gaya.\n"
            "*Appointment ID: {appointment_id}*\n"
            "Clinic: {clinic_name}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "reschedule_failed": "Reschedule fail hua kyunki selected slot ab available nahi hai. Please doosri date/time try kariye.",
        "existing_booking_cancel_failed": "Existing appointment abhi cancel nahi ho paaya. Please baad mein try kariye.",
        "invalid_name": "Please valid name batayiye. Example: Vineeth Raja Banala",
        "name_ack": "Name noted: {name}.",
        "ask_appointment_mode": (
            "Please appointment type choose kariye:\n"
            "1. Online appointment\n"
            "2. Walk-in appointment\n"
            "Please 1, 2, ya 0 number mein reply kariye."
        ),
        "invalid_appointment_mode": "Please 1, 2, ya 0 number mein reply kariye.",
        "appointment_mode_ack": "Noted. Appointment type: {appointment_mode}.",
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
            "1. City Care Clinic, Address: MG Road, Hyderabad. Aaj ke slots: 7\n"
            "2. Sunrise Health Center, Address: KPHB, Hyderabad. Aaj ke slots: 5\n"
            "3. Green Valley Clinic, Address: Gachibowli, Hyderabad. Aaj ke slots: 4"
        ),
        "ask_clinic_header": "Please clinic choose kariye:",
        "invalid_clinic": "Please clinic 1, 2, ya 3 choose kariye.",
        "no_clinic_available": "Abhi booking ke liye koi clinic available nahi hai. Please thodi der baad try kariye.",
        "no_clinic_available_restart": "Abhi koi clinic available nahi hai. Please baad mein try kariye. Restart ke liye 'book appointment' bhejiye.",
        "clinic_ack": "Clinic noted: {clinic_name}, {clinic_address}.",
        "ask_date": "Please preferred appointment date bhejiye (YYYY-MM-DD ya 'tomorrow').",
        "date_options_header": "Please appointment date choose kariye:",
        "availability_date_options_header": "Please availability check karne ke liye date choose kariye:",
        "date_label_today": "Aaj ({date})",
        "date_label_tomorrow": "Kal ({date})",
        "ask_date_options": (
            "Please appointment date choose kariye:\n"
            "1. {date_1}\n"
            "2. {date_2}\n"
            "3. Other date"
        ),
        "ask_date_manual": "Please valid option number choose kariye, ya \"0\" to go back press kariye.",
        "invalid_date": "Invalid date. Please future date YYYY-MM-DD format mein bhejiye.",
        "no_date_available": "{clinic_name} ke liye abhi koi slot available nahi hai. Please doosra clinic choose kariye.",
        "date_ack": "Date noted: {appointment_date}.",
        "ask_time": "Please preferred time share kariye (e.g., 10 am ya 14:30).",
        "choose_slot_header": "Please ek slot choose kariye:",
        "ask_time_slots": (
            "Please time slot choose kariye:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "Please 1, 2, 3, ya 0 reply kariye.\n"
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
            "1. Confirm kariye\n"
            "2. Details badliye\n"
            "0. Go back\n"
            "Reply with 1, 2, or 0."
        ),
        "confirm_prompt": "1. Confirm\n2. Change details\n0. Go back\nReply with 1, 2, or 0.",
        "ask_change_field": (
            "No problem. Kaunsa detail change karna hai?\n"
            "1. Name\n"
            "2. Contact number\n"
            "3. Clinic\n"
            "4. Date\n"
            "5. Time\n"
            "0. Go back\n"
            "Please 1, 2, 3, 4, 5, ya 0 reply kariye."
        ),
        "invalid_change_field": "Please valid option choose kariye (1-5 ya 0).",
        "change_ack": "No problem. Chaliye woh detail update karte hain.",
        "confirmed": (
            "Name: {patient_name}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}"
        ),
        "not_confirmed": "No problem. Booking flow restart kar raha hoon. Please patient ka full name share kariye.",
        "completed_hint": "Yeh appointment flow complete ho chuka hai. Nayi booking ke liye 'book appointment' bhejiye.",
        "db_save_ok": "Appointment successfully book ho gaya.\n*Appointment ID: {appointment_id}*",
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
            "कृपया अपॉइंटमेंट प्रकार चुनें:\n"
            "1. ऑनलाइन परामर्श\n"
            "2. क्लिनिक में परामर्श\n"
            "कृपया 1, 2, या 0 में उत्तर दें।"
        )
    if response_language == "hi" and key == "invalid_appointment_mode":
        return "कृपया 1, 2, या 0 में उत्तर दें।"
    if response_language == "hi" and key == "appointment_mode_ack":
        return f"नोट किया गया। अपॉइंटमेंट प्रकार: {kwargs.get('appointment_mode', '-')}।"
    if key == "change_ack":
        step = kwargs.get("step")
        if source is en:
            prompts = {
                "ASK_NAME": "Please share the patient full name.",
                "ASK_APPOINTMENT_MODE": "Please choose appointment type: 1. Online appointment 2. Walk-in appointment.",
                "ASK_PHONE": "Is this WhatsApp number the contact number? Reply YES or NO. If NO, share a 10-digit number.",
                "ASK_CLINIC": "Please choose clinic 1, 2, or 3.",
                "ASK_DATE": "Please share preferred appointment date (YYYY-MM-DD or 'tomorrow').",
                "ASK_TIME": "Please share preferred time (e.g., 10 am or 14:30).",
            }
        elif source is hi:
            prompts = {
                "ASK_NAME": "कृपया मरीज़ का पूरा नाम बताएं।",
                "ASK_PHONE": "कृपया संपर्क नंबर भेजें (10 अंक)।",
                "ASK_CLINIC": "कृपया क्लिनिक 1, 2 या 3 चुनें।",
                "ASK_DATE": "कृपया पसंदीदा तारीख भेजें (YYYY-MM-DD या 'tomorrow').",
                "ASK_TIME": "कृपया पसंदीदा समय भेजें (जैसे 10 am या 14:30)।",
            }
        else:
            prompts = {
                "ASK_NAME": "Please patient ka full name share kariye.",
                "ASK_APPOINTMENT_MODE": "Please appointment type choose kariye: 1. Online appointment 2. Walk-in appointment.",
                "ASK_PHONE": "Kya yehi WhatsApp number contact number hai? YES ya NO reply kariye. Agar NO, to 10-digit number share kariye.",
                "ASK_CLINIC": "Please clinic 1, 2, ya 3 choose kariye.",
                "ASK_DATE": "Please preferred appointment date bhejiye (YYYY-MM-DD ya 'tomorrow').",
                "ASK_TIME": "Please preferred time share kariye (e.g., 10 am ya 14:30).",
            }
        return source[key].format(**kwargs) + "\n" + prompts.get(step, "")
    template = source.get(key, en.get(key, key))
    message = template.format(**kwargs)
    spacing_keys = {
        "clarify_intent",
        "availability_result_none",
        "availability_result_actions",
        "availability_result_actions_back",
        "confirm_reschedule_summary",
        "confirm_reschedule_prompt",
        "confirm_summary",
        "confirm_prompt",
        "ask_change_field",
        "existing_booking_found",
        "existing_booking_choice_again",
        "max_active_bookings_actions",
    }
    if key in spacing_keys:
        message = _normalize_menu_spacing(message)
    if key in {"db_save_ok", "reschedule_confirmed"}:
        message = _emphasize_patient_id_line(message)
    return message


_QR_MESSAGES = {
    "en": {
        "qr_not_configured": "<h3>QR check-in is not configured.</h3>",
        "qr_not_configured_plain": "QR check-in is not configured.",
        "qr_missing_params": (
            "<h3>QR check-in link is missing required parameters.</h3>"
            "<p>Please scan the correct QR code or open a URL like:</p>"
            "<p><code>/qr/checkin?doctor_id=1&amp;clinic_id=5</code></p>"
        ),
        "qr_invalid_payload": "Invalid QR check-in request payload.",
        "qr_invalid_ids": "Invalid doctor_id/clinic_id.",
        "qr_db_not_configured": "Booking database is not configured.",
        "qr_enter_patient_name": "Please enter patient name.",
        "qr_enter_valid_phone": "Please enter a valid phone number.",
        "qr_doctor_admin_missing": "Doctor/admin mapping is not configured.",
        "qr_active_booking": "You already have an active booking (#{booking_number}){slot_suffix}",
        "qr_confirmed_token": "Appointment confirmed.\nAppointment ID: {token_id}.",
        "qr_estimated_time": "Estimated Time: {estimated_time}.",
        "qr_unable_confirm": "Unable to confirm QR booking: {error}",
        "qr_schedule_unavailable": "Today the doctor has no scheduled appointments for this clinic.",
    },
    "hi": {
        "qr_not_configured": "<h3>QR चेक-इन कॉन्फ़िगर नहीं है।</h3>",
        "qr_not_configured_plain": "QR चेक-इन कॉन्फ़िगर नहीं है।",
        "qr_missing_params": (
            "<h3>QR चेक-इन लिंक में ज़रूरी पैरामीटर नहीं हैं।</h3>"
            "<p>कृपया सही QR कोड स्कैन करें या यह URL खोलें:</p>"
            "<p><code>/qr/checkin?doctor_id=1&amp;clinic_id=5</code></p>"
        ),
        "qr_invalid_payload": "अमान्य QR चेक-इन रिक्वेस्ट पेलोड।",
        "qr_invalid_ids": "अमान्य doctor_id/clinic_id।",
        "qr_db_not_configured": "बुकिंग डेटाबेस कॉन्फ़िगर नहीं है।",
        "qr_enter_patient_name": "कृपया मरीज का नाम दर्ज करें।",
        "qr_enter_valid_phone": "कृपया वैध फोन नंबर दर्ज करें।",
        "qr_doctor_admin_missing": "डॉक्टर/एडमिन मैपिंग कॉन्फ़िगर नहीं है।",
        "qr_active_booking": "आपकी एक सक्रिय बुकिंग पहले से है (#{booking_number}){slot_suffix}",
        "qr_confirmed_token": "अपॉइंटमेंट कन्फर्म हो गई।\nअपॉइंटमेंट आईडी: {token_id}.",
        "qr_estimated_time": "अनुमानित समय: {estimated_time}.",
        "qr_unable_confirm": "QR बुकिंग कन्फर्म नहीं हो पाई: {error}",
        "qr_schedule_unavailable": "आज इस क्लिनिक के लिए डॉक्टर की कोई निर्धारित अपॉइंटमेंट उपलब्ध नहीं है।",
    },
    "hinglish": {
        "qr_not_configured": "<h3>QR check-in configure nahi hai.</h3>",
        "qr_not_configured_plain": "QR check-in configure nahi hai.",
        "qr_missing_params": (
            "<h3>QR check-in link mein required parameters missing hain.</h3>"
            "<p>Please sahi QR code scan kariye ya yeh URL open kariye:</p>"
            "<p><code>/qr/checkin?doctor_id=1&amp;clinic_id=5</code></p>"
        ),
        "qr_invalid_payload": "Invalid QR check-in request payload.",
        "qr_invalid_ids": "Invalid doctor_id/clinic_id.",
        "qr_db_not_configured": "Booking database configure nahi hai.",
        "qr_enter_patient_name": "Please patient name enter kariye.",
        "qr_enter_valid_phone": "Please valid phone number enter kariye.",
        "qr_doctor_admin_missing": "Doctor/admin mapping configure nahi hai.",
        "qr_active_booking": "Aapki ek active booking pehle se hai (#{booking_number}){slot_suffix}",
        "qr_confirmed_token": "Appointment confirm ho gayi.\nAppointment ID: {token_id}.",
        "qr_estimated_time": "Estimated Time: {estimated_time}.",
        "qr_unable_confirm": "QR booking confirm nahi ho payi: {error}",
        "qr_schedule_unavailable": "Aaj is clinic ke liye doctor ki koi scheduled appointment available nahi hai.",
    },
}


def get_qr_message(response_language: str, key: str, **kwargs: object) -> str:
    lang = str(response_language or "en").lower()
    if lang not in _QR_MESSAGES:
        lang = "en"
    source = _QR_MESSAGES[lang]
    template = source.get(key, _QR_MESSAGES["en"].get(key, key))

    if key == "qr_active_booking":
        slot_date = str(kwargs.get("slot_date") or "").strip()
        slot_time = str(kwargs.get("slot_time") or "").strip()
        if slot_date or slot_time:
            if lang == "hi":
                slot_suffix = f" ({slot_date} {slot_time}) पर।".replace("  ", " ").replace("( ", "(").replace(" )", ")")
            elif lang == "hinglish":
                slot_suffix = f" on {slot_date} {slot_time}.".replace("  ", " ").replace(" .", ".")
            else:
                slot_suffix = f" on {slot_date} {slot_time}.".replace("  ", " ").replace(" .", ".")
        else:
            slot_suffix = "."
        kwargs = dict(kwargs)
        kwargs["slot_suffix"] = slot_suffix

    return template.format(**kwargs)
