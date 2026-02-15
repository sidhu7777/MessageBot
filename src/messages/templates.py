def get_message(response_language: str, key: str, **kwargs: object) -> str:
    en = {
        "greeting": "Hello, I am your medical appointment assistant. I can help with booking and doctor availability.",
        "general_help": "I can help with appointment booking or doctor availability. Tell me what you need.",
        "intent_ack": "Sure, I can help you book an appointment.",
        "availability_intro": "Sure, I can help check doctor availability. Please share doctor name and preferred date.",
        "availability_ask": "Please share doctor name and preferred date (YYYY-MM-DD or 'tomorrow') to check availability.",
        "availability_ask_doctor": "Please share the doctor name to check availability.",
        "availability_ask_date": "Please share preferred date (YYYY-MM-DD or 'tomorrow') to check availability.",
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
        "invalid_name": "Please provide a valid name. Example: Vineeth Raja Banala",
        "name_ack": "Thank you, {name}.",
        "ask_patient_type": "Is the patient old or new?\n1. New\n2. Old",
        "invalid_patient_type": "Please reply with 'old' or 'new'.",
        "patient_type_ack": "Noted. Patient type: {patient_type}.",
        "ask_age": "Please share patient age.",
        "invalid_age": "Please share a valid age between 1 and 120.",
        "age_ack": "Age noted: {age}.",
        "ask_gender": "Please share patient gender:\n1. Male\n2. Female\n3. Other",
        "invalid_gender": "Please reply with gender as male, female, or other.",
        "gender_ack": "Gender noted: {gender}.",
        "ask_phone": (
            "Please confirm contact number:\n"
            "1. Same as this WhatsApp number\n"
            "2. Share a different number (10 digits)"
        ),
        "invalid_phone_same_missing": "I could not read the WhatsApp number. Please share a 10-digit contact number.",
        "invalid_phone": "Please share a valid 10-digit contact number.",
        "phone_ack": "Contact number noted: {phone_number}.",
        "ask_clinic": (
            "Please choose clinic:\n"
            "1. City Care Clinic | MG Road, Hyderabad | Slots today: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | Slots today: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | Slots today: 4"
        ),
        "invalid_clinic": "Please choose clinic 1, 2, or 3.",
        "clinic_ack": "Clinic noted: {clinic_name}, {clinic_address}.",
        "ask_reason": (
            "Select reason (you can choose multiple like 1,3):\n"
            "1. Fever\n"
            "2. Headache\n"
            "3. Stomach pain\n"
            "4. Cold\n"
            "5. Other (type reason)"
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
            "2. {date_2}\n"
            "3. {date_3}\n"
            "4. Other date"
        ),
        "ask_date_manual": "Please type preferred date in YYYY-MM-DD format.",
        "invalid_date": "Invalid date. Please send a future date in YYYY-MM-DD format.",
        "date_ack": "Date noted: {appointment_date}.",
        "ask_time": "Please share preferred time (e.g., 10 am or 14:30).",
        "ask_time_slots": (
            "Please choose a time slot:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "Or type another preferred time."
        ),
        "time_not_available": "Requested time {requested_time} is not available.",
        "time_ack": "Time noted: {appointment_time}.",
        "invalid_time": "Invalid time format. Example: 10 am or 14:30",
        "confirm_summary": (
            "Please confirm your appointment details:\n"
            "Name: {patient_name}\n"
            "Patient type: {patient_type}\n"
            "Age: {age}\n"
            "Gender: {gender}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Reason: {reason}\n"
            "Symptoms: {symptoms}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Reply YES to confirm or NO to restart."
        ),
        "confirm_prompt": "Please reply YES to confirm or NO to restart.",
        "change_ack": (
            "No problem. Let's update that detail."
        ),
        "confirmed": (
            "Appointment request confirmed.\n"
            "Name: {patient_name}\n"
            "Patient type: {patient_type}\n"
            "Age: {age}\n"
            "Gender: {gender}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Reason: {reason}\n"
            "Symptoms: {symptoms}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Send 'new appointment' for another booking."
        ),
        "not_confirmed": "No problem. Restarting booking flow. Please share the patient full name.",
        "completed_hint": "This appointment flow is complete. Send 'new appointment' to start another.",
        "db_save_ok": "Database booking saved. Reference ID: {appointment_id}.",
        "db_save_failed": "Booking confirmation received, but database save is pending manual follow-up.",
        "ended": "Understood. I have ended the process. Send 'book appointment' whenever you want to start again.",
        "cancelled_hint": "Process is ended. Send 'book appointment' to start a new booking.",
        "restart": "Restarting the appointment flow.",
    }

    hi = {
        "greeting": "नमस्ते, मैं आपका मेडिकल अपॉइंटमेंट असिस्टेंट हूँ। मैं बुकिंग और डॉक्टर उपलब्धता में मदद कर सकता हूँ।",
        "general_help": "मैं अपॉइंटमेंट बुकिंग या डॉक्टर उपलब्धता में मदद कर सकता हूँ। कृपया अपनी आवश्यकता बताएं।",
        "intent_ack": "ठीक है, मैं आपकी अपॉइंटमेंट बुक करने में मदद कर सकता हूँ।",
        "availability_intro": "ठीक है, मैं डॉक्टर की उपलब्धता देखने में मदद कर सकता हूँ। कृपया डॉक्टर का नाम और पसंदीदा तारीख बताएं।",
        "availability_ask": "उपलब्धता देखने के लिए कृपया डॉक्टर का नाम और तारीख भेजें (YYYY-MM-DD या 'tomorrow').",
        "availability_ask_doctor": "कृपया उपलब्धता देखने के लिए डॉक्टर का नाम बताएं।",
        "availability_ask_date": "कृपया उपलब्धता देखने के लिए तारीख बताएं (YYYY-MM-DD या 'tomorrow').",
        "availability_noted": (
            "नोट किया गया। आप Dr. {availability_doctor} के लिए {availability_date} की उपलब्धता पूछ रहे हैं।\n"
            "बुकिंग जारी रखने के लिए 'book appointment' भेजें।"
        ),
        "empty_input": "कृपया संदेश भेजें ताकि मैं आपकी मदद कर सकूँ।",
        "no_intent": (
            "शुरू करने के लिए कृपया लिखें: 'I need to book an appointment'."
        ),
        "clarify_intent": (
            "कृपया एक विकल्प चुनें:\n"
            "1. अपॉइंटमेंट बुक करें\n"
            "2. डॉक्टर उपलब्धता देखें"
        ),
        "final_booking_check": "क्या आप अभी मेडिकल अपॉइंटमेंट बुक करना चाहते हैं? कृपया YES या NO में जवाब दें।",
        "non_scope_final": (
            "माफ़ कीजिए, मैं मेडिकल अपॉइंटमेंट असिस्टेंट हूँ और केवल बुकिंग या डॉक्टर उपलब्धता में मदद कर सकता हूँ।\n"
            "शुरू करने के लिए कभी भी 'book appointment' भेजें।"
        ),
        "ask_name": "कृपया मरीज का पूरा नाम बताएं।",
        "invalid_name": "कृपया सही नाम बताएं। उदाहरण: Vineeth Raja Banala",
        "name_ack": "धन्यवाद, {name}।",
        "ask_patient_type": "मरीज पुराना है या नया?\n1. New\n2. Old",
        "invalid_patient_type": "कृपया 'old' या 'new' में उत्तर दें।",
        "patient_type_ack": "नोट किया गया। मरीज का प्रकार: {patient_type}।",
        "ask_age": "कृपया मरीज की आयु बताएं।",
        "invalid_age": "कृपया 1 से 120 के बीच सही आयु बताएं।",
        "age_ack": "आयु नोट की गई: {age}।",
        "ask_gender": "कृपया मरीज का जेंडर बताएं:\n1. Male\n2. Female\n3. Other",
        "invalid_gender": "कृपया जेंडर male, female, या other में बताएं।",
        "gender_ack": "जेंडर नोट किया गया: {gender}।",
        "ask_phone": (
            "कृपया संपर्क नंबर पुष्टि करें:\n"
            "1. यही WhatsApp नंबर उपयोग करें\n"
            "2. अलग नंबर भेजें (10 अंक)"
        ),
        "invalid_phone_same_missing": "WhatsApp नंबर पढ़ा नहीं जा सका। कृपया 10 अंकों का नंबर भेजें।",
        "invalid_phone": "कृपया 10 अंकों का सही संपर्क नंबर भेजें।",
        "phone_ack": "संपर्क नंबर नोट किया गया: {phone_number}।",
        "ask_clinic": (
            "कृपया क्लिनिक चुनें:\n"
            "1. City Care Clinic | MG Road, Hyderabad | आज स्लॉट: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | आज स्लॉट: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | आज स्लॉट: 4"
        ),
        "invalid_clinic": "कृपया क्लिनिक 1, 2 या 3 चुनें।",
        "clinic_ack": "क्लिनिक नोट किया गया: {clinic_name}, {clinic_address}।",
        "ask_reason": (
            "कारण चुनें (एक से अधिक चुन सकते हैं, जैसे 1,3):\n"
            "1. Fever\n"
            "2. Headache\n"
            "3. Stomach pain\n"
            "4. Cold\n"
            "5. Other (कारण टाइप करें)"
        ),
        "ask_reason_other": "कृपया कारण टाइप करें।",
        "invalid_reason_option": "कृपया सही कारण विकल्प चुनें, या कारण टेक्स्ट में लिखें।",
        "invalid_reason": "कृपया कारण थोड़े स्पष्ट रूप में बताएं।",
        "reason_ack": "कारण नोट किया गया।",
        "ask_symptoms": "कृपया लक्षण बताएं।",
        "invalid_symptoms": "कृपया लक्षण थोड़े स्पष्ट रूप में बताएं।",
        "symptoms_ack": "लक्षण नोट किए गए।",
        "ask_date": "कृपया पसंदीदा तारीख भेजें (YYYY-MM-DD या 'tomorrow').",
        "ask_date_options": (
            "कृपया अपॉइंटमेंट तारीख चुनें:\n"
            "1. {date_1}\n"
            "2. {date_2}\n"
            "3. {date_3}\n"
            "4. Other date"
        ),
        "ask_date_manual": "कृपया तारीख YYYY-MM-DD फॉर्मेट में टाइप करें।",
        "invalid_date": "तारीख अमान्य है। कृपया भविष्य की तारीख YYYY-MM-DD फ़ॉर्मेट में भेजें।",
        "date_ack": "तारीख नोट की गई: {appointment_date}।",
        "ask_time": "कृपया पसंदीदा समय भेजें (जैसे 10 am या 14:30)।",
        "ask_time_slots": (
            "कृपया स्लॉट चुनें:\n"
            "1. {slot_1}\n"
            "2. {slot_2}\n"
            "3. {slot_3}\n"
            "या दूसरा समय टाइप करें।"
        ),
        "time_not_available": "मांगा गया समय {requested_time} उपलब्ध नहीं है।",
        "time_ack": "समय नोट किया गया: {appointment_time}।",
        "invalid_time": "समय का फ़ॉर्मेट अमान्य है। उदाहरण: 10 am या 14:30",
        "confirm_summary": (
            "कृपया अपॉइंटमेंट विवरण की पुष्टि करें:\n"
            "नाम: {patient_name}\n"
            "मरीज का प्रकार: {patient_type}\n"
            "आयु: {age}\n"
            "जेंडर: {gender}\n"
            "संपर्क: {phone_number}\n"
            "क्लिनिक: {clinic_name}\n"
            "क्लिनिक पता: {clinic_address}\n"
            "कारण: {reason}\n"
            "लक्षण: {symptoms}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}\n"
            "पुष्टि के लिए YES और दोबारा शुरू करने के लिए NO भेजें।"
        ),
        "confirm_prompt": "कृपया पुष्टि के लिए YES या दोबारा शुरू करने के लिए NO भेजें।",
        "change_ack": (
            "ठीक है। हम वह विवरण अपडेट करते हैं।"
        ),
        "confirmed": (
            "अपॉइंटमेंट अनुरोध पुष्टि हो गया है।\n"
            "नाम: {patient_name}\n"
            "मरीज का प्रकार: {patient_type}\n"
            "आयु: {age}\n"
            "जेंडर: {gender}\n"
            "संपर्क: {phone_number}\n"
            "क्लिनिक: {clinic_name}\n"
            "क्लिनिक पता: {clinic_address}\n"
            "कारण: {reason}\n"
            "लक्षण: {symptoms}\n"
            "तारीख: {appointment_date}\n"
            "समय: {appointment_time}\n"
            "नई बुकिंग के लिए 'new appointment' भेजें।"
        ),
        "not_confirmed": "कोई बात नहीं। प्रक्रिया दोबारा शुरू कर रहा हूँ। कृपया मरीज का पूरा नाम बताएं।",
        "completed_hint": "यह बुकिंग पूरी हो चुकी है। नई बुकिंग के लिए 'new appointment' भेजें।",
        "db_save_ok": "बुकिंग डेटाबेस में सेव हो गई। रेफरेंस आईडी: {appointment_id}।",
        "db_save_failed": "बुकिंग कन्फर्म हुई, लेकिन डेटाबेस सेव लंबित है। मैनुअल फॉलो-अप आवश्यक है।",
        "ended": "ठीक है, प्रक्रिया समाप्त कर दी गई है। दोबारा शुरू करने के लिए 'book appointment' भेजें।",
        "cancelled_hint": "प्रक्रिया समाप्त है। नई बुकिंग के लिए 'book appointment' भेजें।",
        "restart": "अपॉइंटमेंट प्रक्रिया दोबारा शुरू की जा रही है।",
    }

    hinglish = {
        "greeting": "Hello, main aapka medical appointment assistant hoon. Main booking aur doctor availability mein help kar sakta hoon.",
        "general_help": "Main appointment booking ya doctor availability mein help kar sakta hoon. Aapko kya chahiye?",
        "intent_ack": "Theek hai, main aapki appointment booking mein help kar sakta hoon.",
        "availability_intro": "Theek hai, main doctor availability check karne mein help kar sakta hoon. Doctor name aur preferred date share kariye.",
        "availability_ask": "Availability check ke liye doctor name aur preferred date bhejiye (YYYY-MM-DD ya 'tomorrow').",
        "availability_ask_doctor": "Availability check ke liye doctor name share kariye.",
        "availability_ask_date": "Availability check ke liye preferred date bhejiye (YYYY-MM-DD ya 'tomorrow').",
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
        "invalid_name": "Please valid name batayiye. Example: Vineeth Raja Banala",
        "name_ack": "Thank you, {name}.",
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
            "Please contact number confirm kariye:\n"
            "1. Same WhatsApp number use karo\n"
            "2. Different number share karo (10 digits)"
        ),
        "invalid_phone_same_missing": "WhatsApp number read nahi hua. Please 10-digit contact number share kariye.",
        "invalid_phone": "Please valid 10-digit contact number bhejiye.",
        "phone_ack": "Contact number noted: {phone_number}.",
        "ask_clinic": (
            "Please clinic choose kariye:\n"
            "1. City Care Clinic | MG Road, Hyderabad | Aaj ke slots: 7\n"
            "2. Sunrise Health Center | KPHB, Hyderabad | Aaj ke slots: 5\n"
            "3. Green Valley Clinic | Gachibowli, Hyderabad | Aaj ke slots: 4"
        ),
        "invalid_clinic": "Please clinic 1, 2, ya 3 choose kariye.",
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
            "3. {date_3}\n"
            "4. Other date"
        ),
        "ask_date_manual": "Please date YYYY-MM-DD format mein type kariye.",
        "invalid_date": "Invalid date. Please future date YYYY-MM-DD format mein bhejiye.",
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
        "time_ack": "Time noted: {appointment_time}.",
        "invalid_time": "Invalid time format. Example: 10 am ya 14:30",
        "confirm_summary": (
            "Please appointment details confirm kariye:\n"
            "Name: {patient_name}\n"
            "Patient type: {patient_type}\n"
            "Age: {age}\n"
            "Gender: {gender}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Reason: {reason}\n"
            "Symptoms: {symptoms}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Confirm ke liye YES ya restart ke liye NO bhejiye."
        ),
        "confirm_prompt": "Please confirm ke liye YES ya restart ke liye NO bhejiye.",
        "change_ack": "No problem. Chaliye woh detail update karte hain.",
        "confirmed": (
            "Appointment request confirm ho gaya.\n"
            "Name: {patient_name}\n"
            "Patient type: {patient_type}\n"
            "Age: {age}\n"
            "Gender: {gender}\n"
            "Contact: {phone_number}\n"
            "Clinic: {clinic_name}\n"
            "Clinic address: {clinic_address}\n"
            "Reason: {reason}\n"
            "Symptoms: {symptoms}\n"
            "Date: {appointment_date}\n"
            "Time: {appointment_time}\n"
            "Nayi booking ke liye 'new appointment' bhejiye."
        ),
        "not_confirmed": "No problem. Booking flow restart kar raha hoon. Please patient ka full name share kariye.",
        "completed_hint": "Yeh appointment flow complete ho chuka hai. Nayi booking ke liye 'new appointment' bhejiye.",
        "db_save_ok": "Booking database mein save ho gayi. Reference ID: {appointment_id}.",
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
    if key == "change_ack":
        step = kwargs.get("step")
        if source is en:
            prompts = {
                "ASK_NAME": "Please share the patient full name.",
                "ASK_PATIENT_TYPE": "Is the patient old or new?",
                "ASK_AGE": "Please share patient age.",
                "ASK_GENDER": "Please share patient gender: male, female, or other.",
                "ASK_PHONE": "Please share contact number (10 digits).",
                "ASK_CLINIC": "Please choose clinic 1, 2, or 3.",
                "ASK_REASON": "What is the reason for the appointment?",
                "ASK_SYMPTOMS": "Please share the symptoms.",
                "ASK_DATE": "Please share preferred appointment date (YYYY-MM-DD or 'tomorrow').",
                "ASK_TIME": "Please share preferred time (e.g., 10 am or 14:30).",
            }
        elif source is hi:
            prompts = {
                "ASK_NAME": "कृपया मरीज का पूरा नाम बताएं।",
                "ASK_PATIENT_TYPE": "मरीज पुराना है या नया?",
                "ASK_AGE": "कृपया मरीज की आयु बताएं।",
                "ASK_GENDER": "कृपया मरीज का जेंडर बताएं: male, female, या other।",
                "ASK_PHONE": "कृपया संपर्क नंबर भेजें (10 अंक)।",
                "ASK_CLINIC": "कृपया क्लिनिक 1, 2 या 3 चुनें।",
                "ASK_REASON": "अपॉइंटमेंट का कारण क्या है?",
                "ASK_SYMPTOMS": "कृपया लक्षण बताएं।",
                "ASK_DATE": "कृपया पसंदीदा तारीख भेजें (YYYY-MM-DD या 'tomorrow').",
                "ASK_TIME": "कृपया पसंदीदा समय भेजें (जैसे 10 am या 14:30)।",
            }
        else:
            prompts = {
                "ASK_NAME": "Please patient ka full name share kariye.",
                "ASK_PATIENT_TYPE": "Patient old hai ya new?",
                "ASK_AGE": "Please patient age share kariye.",
                "ASK_GENDER": "Please patient gender share kariye: male, female, ya other.",
                "ASK_PHONE": "Please contact number share kariye (10 digits).",
                "ASK_CLINIC": "Please clinic 1, 2, ya 3 choose kariye.",
                "ASK_REASON": "Appointment ka reason kya hai?",
                "ASK_SYMPTOMS": "Please symptoms share kariye.",
                "ASK_DATE": "Please preferred appointment date bhejiye (YYYY-MM-DD ya 'tomorrow').",
                "ASK_TIME": "Please preferred time share kariye (e.g., 10 am ya 14:30).",
            }
        return source[key].format(**kwargs) + "\n" + prompts.get(step, "")
    return source[key].format(**kwargs)
