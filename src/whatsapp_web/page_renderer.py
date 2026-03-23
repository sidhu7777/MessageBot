import html as html_escape
import json


def render_whatsapp_web_page_html(
    *,
    doctor_id: int,
    doctor_name: str,
    language: str = "en",
    lock_language: bool = False,
) -> str:
    doctor_name_safe = html_escape.escape(doctor_name or "Doctor")
    lang = language if language in {"en", "hi", "hinglish"} else "en"
    ui = {
        "en": {
            "kicker": "WhatsApp Booking Link",
            "title": f"Welcome to Dr. {doctor_name_safe} Clinic",
            "subtitle": "Use this page to book, reschedule, or cancel an appointment.",
            "name_label": "Full Name",
            "phone_label": "Phone Number",
            "booking_for_label": "Booking For",
            "booking_for_self": "Self",
            "booking_for_other": "Someone Else",
            "continue": "Continue",
            "clinic_label": "Choose Clinic",
            "date_label": "Choose Date",
            "period_label": "Choose Time Period",
            "time_label": "Choose Time",
            "book_submit": "Book Appointment",
            "reschedule_submit": "Confirm Reschedule",
            "manage_title": "Existing Appointment Found",
            "booking_title": "Book New Appointment",
            "reschedule_title": "Reschedule Appointment",
            "close": "Close",
            "modal_ok": "Appointment Confirmed",
            "modal_warn": "Booking Update",
            "modal_err": "Booking Status",
        },
        "hi": {
            "kicker": "व्हाट्सऐप बुकिंग लिंक",
            "title": f"Dr. {doctor_name_safe} Clinic में आपका स्वागत है",
            "subtitle": "इस पेज से अपॉइंटमेंट बुक, रीशेड्यूल या कैंसल करें।",
            "name_label": "पूरा नाम",
            "phone_label": "फोन नंबर",
            "booking_for_label": "किसके लिए बुकिंग",
            "booking_for_self": "स्वयं",
            "booking_for_other": "किसी और के लिए",
            "continue": "आगे बढ़ें",
            "clinic_label": "क्लिनिक चुनें",
            "date_label": "तारीख चुनें",
            "period_label": "समय अवधि चुनें",
            "time_label": "समय चुनें",
            "book_submit": "अपॉइंटमेंट बुक करें",
            "reschedule_submit": "रीशेड्यूल कन्फर्म करें",
            "manage_title": "मौजूदा अपॉइंटमेंट मिली",
            "booking_title": "नई अपॉइंटमेंट बुक करें",
            "reschedule_title": "अपॉइंटमेंट रीशेड्यूल करें",
            "close": "बंद करें",
            "modal_ok": "अपॉइंटमेंट कन्फर्म हुई",
            "modal_warn": "बुकिंग अपडेट",
            "modal_err": "बुकिंग स्थिति",
        },
        "hinglish": {
            "kicker": "WhatsApp Booking Link",
            "title": f"Dr. {doctor_name_safe} Clinic mein aapka swagat hai",
            "subtitle": "Is page se appointment book, reschedule ya cancel kariye.",
            "name_label": "Full Name",
            "phone_label": "Phone Number",
            "booking_for_label": "Booking Kis Ke Liye",
            "booking_for_self": "Self",
            "booking_for_other": "Someone Else",
            "continue": "Continue kariye",
            "clinic_label": "Clinic choose kariye",
            "date_label": "Date choose kariye",
            "period_label": "Time period choose kariye",
            "time_label": "Time choose kariye",
            "book_submit": "Appointment Book kariye",
            "reschedule_submit": "Reschedule Confirm kariye",
            "manage_title": "Existing Appointment Mili",
            "booking_title": "New Appointment Book kariye",
            "reschedule_title": "Appointment Reschedule kariye",
            "close": "Close",
            "modal_ok": "Appointment Confirmed",
            "modal_warn": "Booking Update",
            "modal_err": "Booking Status",
        },
    }[lang]
    texts_json = json.dumps(
        {
            "en": {
                "kicker": "WhatsApp Booking Link",
                "title": f"Welcome to Dr. {doctor_name_safe} Clinic",
                "subtitle": "Use this page to book, reschedule, or cancel an appointment.",
                "nameLabel": "Full Name",
                "phoneLabel": "Phone Number",
                "bookingForLabel": "Booking For",
                "bookingForSelf": "Self",
                "bookingForOther": "Someone Else",
                "continue": "Continue",
                "clinicLabel": "Choose Clinic",
                "dateLabel": "Choose Date",
                "periodLabel": "Choose Time Period",
                "timeLabel": "Choose Time",
                "bookSubmit": "Book Appointment",
                "rescheduleSubmit": "Confirm Reschedule",
                "manageTitle": "Existing Appointment Found",
                "bookingTitle": "Book New Appointment",
                "rescheduleTitle": "Reschedule Appointment",
                "existingHint": "You already have an active appointment. Choose cancel or reschedule.",
                "noClinics": "No clinics available.",
                "noDates": "No dates available.",
                "noTimes": "No times available.",
                "cancelBtn": "Cancel Appointment",
                "rescheduleBtn": "Reschedule",
                "backToBooking": "Book New Instead",
                "bookInsteadBlocked": "Book New Instead is only available for Someone Else. Cancel or reschedule the existing appointment to continue for self.",
                "close": "Close",
                "modalOk": "Appointment Confirmed",
                "modalBooked": "Appointment Confirmed",
                "modalRescheduled": "Appointment Rescheduled",
                "modalCancelled": "Appointment Cancelled",
                "modalWarn": "Booking Update",
                "modalErr": "Booking Status",
                "continueLoading": "Checking appointment history...",
                "loadingDates": "Loading dates...",
                "loadingTimes": "Loading times...",
                "loadingPeriods": "Loading time periods...",
                "submitting": "Submitting...",
                "serverError": "Server error. Please try again.",
                "requestFailed": "Request failed.",
                "submitFailed": "Unable to submit right now. Please try again.",
                "missingDoctor": "Doctor ID missing.",
                "missingIdentity": "Please enter name and phone number.",
                "missingBookingFields": "Please choose clinic, date, and time.",
                "confirmCancel": "Do you want to cancel this appointment?",
                "confirmCancelTitle": "Cancel Appointment",
                "confirmYes": "Yes, Cancel",
                "confirmNo": "No",
                "appointmentLabel": "Appointment",
                "clinicPrefix": "Clinic",
                "datePrefix": "Date",
                "timePrefix": "Time",
                "appointmentIdPrefix": "Appointment ID",
            },
            "hi": {
                "kicker": "व्हाट्सऐप बुकिंग लिंक",
                "title": f"Dr. {doctor_name_safe} Clinic में आपका स्वागत है",
                "subtitle": "इस पेज से अपॉइंटमेंट बुक, रीशेड्यूल या कैंसल करें।",
                "nameLabel": "पूरा नाम",
                "phoneLabel": "फोन नंबर",
                "bookingForLabel": "किसके लिए बुकिंग",
                "bookingForSelf": "स्वयं",
                "bookingForOther": "किसी और के लिए",
                "continue": "आगे बढ़ें",
                "clinicLabel": "क्लिनिक चुनें",
                "dateLabel": "तारीख चुनें",
                "periodLabel": "समय अवधि चुनें",
                "timeLabel": "समय चुनें",
                "bookSubmit": "अपॉइंटमेंट बुक करें",
                "rescheduleSubmit": "रीशेड्यूल कन्फर्म करें",
                "manageTitle": "मौजूदा अपॉइंटमेंट मिली",
                "bookingTitle": "नई अपॉइंटमेंट बुक करें",
                "rescheduleTitle": "अपॉइंटमेंट रीशेड्यूल करें",
                "existingHint": "आपकी सक्रिय अपॉइंटमेंट पहले से है। कैंसल या रीशेड्यूल चुनें।",
                "noClinics": "कोई क्लिनिक उपलब्ध नहीं है।",
                "noDates": "कोई तारीख उपलब्ध नहीं है।",
                "noTimes": "कोई समय उपलब्ध नहीं है।",
                "cancelBtn": "अपॉइंटमेंट कैंसल करें",
                "rescheduleBtn": "रीशेड्यूल करें",
                "backToBooking": "नई बुकिंग करें",
                "bookInsteadBlocked": "नई बुकिंग केवल किसी और के लिए उपलब्ध है। स्वयं के लिए आगे बढ़ने से पहले मौजूदा अपॉइंटमेंट कैंसल या रीशेड्यूल करें।",
                "close": "बंद करें",
                "modalOk": "अपॉइंटमेंट कन्फर्म हुई",
                "modalBooked": "अपॉइंटमेंट कन्फर्म हुई",
                "modalRescheduled": "अपॉइंटमेंट रीशेड्यूल हुई",
                "modalCancelled": "अपॉइंटमेंट कैंसल हुई",
                "modalWarn": "बुकिंग अपडेट",
                "modalErr": "बुकिंग स्थिति",
                "continueLoading": "अपॉइंटमेंट हिस्ट्री देखी जा रही है...",
                "loadingDates": "तारीखें लोड हो रही हैं...",
                "loadingTimes": "समय लोड हो रहे हैं...",
                "loadingPeriods": "समय अवधि लोड हो रही है...",
                "submitting": "सबमिट हो रहा है...",
                "serverError": "सर्वर त्रुटि। कृपया फिर से कोशिश करें।",
                "requestFailed": "रिक्वेस्ट असफल रही।",
                "submitFailed": "अभी सबमिट नहीं हो पाया। कृपया फिर से कोशिश करें।",
                "missingDoctor": "डॉक्टर आईडी नहीं मिली।",
                "missingIdentity": "कृपया नाम और फोन नंबर दर्ज करें।",
                "missingBookingFields": "कृपया क्लिनिक, तारीख और समय चुनें।",
                "confirmCancel": "क्या आप यह अपॉइंटमेंट कैंसल करना चाहते हैं?",
                "confirmCancelTitle": "अपॉइंटमेंट कैंसल करें",
                "confirmYes": "हाँ, कैंसल करें",
                "confirmNo": "नहीं",
                "appointmentLabel": "अपॉइंटमेंट",
                "clinicPrefix": "क्लिनिक",
                "datePrefix": "तारीख",
                "timePrefix": "समय",
                "appointmentIdPrefix": "अपॉइंटमेंट आईडी",
            },
            "hinglish": {
                "kicker": "WhatsApp Booking Link",
                "title": f"Dr. {doctor_name_safe} Clinic mein aapka swagat hai",
                "subtitle": "Is page se appointment book, reschedule ya cancel kariye.",
                "nameLabel": "Full Name",
                "phoneLabel": "Phone Number",
                "bookingForLabel": "Booking Kis Ke Liye",
                "bookingForSelf": "Self",
                "bookingForOther": "Someone Else",
                "continue": "Continue kariye",
                "clinicLabel": "Clinic choose kariye",
                "dateLabel": "Date choose kariye",
                "periodLabel": "Time period choose kariye",
                "timeLabel": "Time choose kariye",
                "bookSubmit": "Appointment Book kariye",
                "rescheduleSubmit": "Reschedule Confirm kariye",
                "manageTitle": "Existing Appointment Mili",
                "bookingTitle": "New Appointment Book kariye",
                "rescheduleTitle": "Appointment Reschedule kariye",
                "existingHint": "Aapki active appointment pehle se hai. Cancel ya reschedule choose kariye.",
                "noClinics": "Koi clinic available nahi hai.",
                "noDates": "Koi date available nahi hai.",
                "noTimes": "Koi time available nahi hai.",
                "cancelBtn": "Appointment Cancel kariye",
                "rescheduleBtn": "Reschedule kariye",
                "backToBooking": "Nayi booking kariye",
                "bookInsteadBlocked": "Nayi booking sirf Someone Else ke liye available hai. Self ke liye aage badhne se pehle existing appointment cancel ya reschedule kariye.",
                "close": "Close",
                "modalOk": "Appointment Confirmed",
                "modalBooked": "Appointment Confirmed",
                "modalRescheduled": "Appointment Rescheduled",
                "modalCancelled": "Appointment Cancelled",
                "modalWarn": "Booking Update",
                "modalErr": "Booking Status",
                "continueLoading": "Appointment history check ho rahi hai...",
                "loadingDates": "Dates load ho rahi hain...",
                "loadingTimes": "Times load ho rahe hain...",
                "loadingPeriods": "Time periods load ho rahe hain...",
                "submitting": "Submit ho raha hai...",
                "serverError": "Server error. Please dobara try kariye.",
                "requestFailed": "Request fail hui.",
                "submitFailed": "Abhi submit nahi ho paya. Please dobara try kariye.",
                "missingDoctor": "Doctor ID missing hai.",
                "missingIdentity": "Please name aur phone number enter kariye.",
                "missingBookingFields": "Please clinic, date aur time choose kariye.",
                "confirmCancel": "Kya aap yeh appointment cancel karna chahte hain?",
                "confirmCancelTitle": "Appointment Cancel kariye",
                "confirmYes": "Haan, Cancel kariye",
                "confirmNo": "Nahi",
                "appointmentLabel": "Appointment",
                "clinicPrefix": "Clinic",
                "datePrefix": "Date",
                "timePrefix": "Time",
                "appointmentIdPrefix": "Appointment ID",
            },
        },
        ensure_ascii=False,
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>WhatsApp Booking</title>
  <style>
    :root {{
      --bg: linear-gradient(140deg, #f6f7f2 0%, #e5efe0 60%, #d7e7dc 100%);
      --card: #ffffff;
      --ink: #1d2a23;
      --muted: #5b6e62;
      --accent: #0f766e;
      --accent-soft: #d3f2ee;
      --ok: #0a7a4f;
      --warn: #a85810;
      --danger: #9f2d2d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background: var(--bg);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 18px;
    }}
    .card {{
      width: min(860px, 100%);
      background: var(--card);
      border-radius: 20px;
      box-shadow: 0 20px 50px rgba(8, 38, 34, 0.14);
      overflow: hidden;
      border: 1px solid #e4efe8;
    }}
    .hero {{
      padding: 28px 24px 12px 24px;
      background:
        radial-gradient(circle at 85% 15%, #c5f3ea 0, rgba(197,243,234,0) 46%),
        radial-gradient(circle at 20% 0%, #ebf9f4 0, rgba(235,249,244,0) 52%);
    }}
    .kicker {{
      display: inline-block;
      background: var(--accent-soft);
      color: #0d645d;
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 12px 0 8px 0;
      font-size: 28px;
      line-height: 1.2;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 15px;
    }}
    .section {{
      padding: 20px 24px 24px 24px;
      display: grid;
      gap: 14px;
      border-top: 1px solid #edf3ef;
    }}
    .hidden {{ display: none !important; }}
    .section.hidden {{ display: none; }}
    .field-wrap.hidden {{ display: none; }}
    .section-title {{
      font-size: 18px;
      font-weight: 700;
      margin: 0;
    }}
    .section-subtitle {{
      margin: -6px 0 0 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    @media (min-width: 640px) {{
      .grid.two {{ grid-template-columns: 1fr 1fr; }}
      .grid.three {{ grid-template-columns: 1fr 1fr 1fr; }}
    }}
    label {{
      display: grid;
      gap: 6px;
      font-weight: 600;
      font-size: 14px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 12px 12px;
      font-size: 15px;
      outline: none;
      transition: border-color .2s, box-shadow .2s;
      background: white;
    }}
    input:focus, select:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15,118,110,0.13);
    }}
    .booking-for {{
      display: grid;
      gap: 10px;
    }}
    .booking-for-group {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .booking-for-option {{
      display: flex;
      align-items: center;
      gap: 8px;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 10px 12px;
      background: #fff;
      font-weight: 600;
      cursor: pointer;
    }}
    .booking-for-option input {{
      width: auto;
      margin: 0;
      padding: 0;
      box-shadow: none;
    }}
    button {{
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      color: white;
      background: linear-gradient(135deg, #0f766e, #0b5a53);
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button.alt {{
      background: linear-gradient(135deg, #6a7b70, #506158);
    }}
    button.danger {{
      background: linear-gradient(135deg, #9f2d2d, #7b2323);
    }}
    button:disabled {{
      opacity: .7;
      cursor: not-allowed;
    }}
    .appt-list {{
      display: grid;
      gap: 12px;
    }}
    .appt-card {{
      border: 1px solid #dce9e2;
      background: #f8fcfa;
      border-radius: 14px;
      padding: 14px;
      display: grid;
      gap: 10px;
    }}
    .appt-meta {{
      display: grid;
      gap: 4px;
      color: var(--ink);
      font-size: 14px;
    }}
    .appt-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .status-line {{
      border: 1px solid #dce9e2;
      background: #f8fcfa;
      border-radius: 12px;
      padding: 12px;
      min-height: 50px;
      font-size: 14px;
      white-space: pre-wrap;
      display: none;
    }}
    .status-line.visible {{ display: block; }}
    .modal {{
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(7, 24, 22, 0.48);
      padding: 20px;
      z-index: 999;
    }}
    .modal.open {{ display: flex; }}
    .modal-card {{
      width: min(560px, 100%);
      background: #fff;
      border-radius: 18px;
      box-shadow: 0 24px 60px rgba(10, 30, 26, 0.28);
      border: 1px solid #dfece5;
      overflow: hidden;
    }}
    .modal-head {{
      padding: 18px 20px 12px 20px;
      border-bottom: 1px solid #ebf1ed;
      font-size: 20px;
      font-weight: 700;
    }}
    .modal-head.ok {{ color: var(--ok); }}
    .modal-head.warn {{ color: var(--warn); }}
    .modal-head.err {{ color: var(--danger); }}
    .modal-body {{
      padding: 18px 20px;
      line-height: 1.55;
      font-size: 15px;
      white-space: normal;
    }}
    .modal-actions {{
      padding: 0 20px 20px 20px;
      display: flex;
      justify-content: flex-end;
      gap: 12px;
    }}
    .result-lines {{
      display: grid;
      gap: 12px;
    }}
    .result-summary {{
      display: grid;
      gap: 8px;
      padding-top: 4px;
    }}
    .result-row {{
      font-size: 18px;
      line-height: 1.4;
      font-weight: 700;
      color: var(--ink);
    }}
    .result-row strong {{
      font-weight: 800;
    }}
    .modal-note {{
      font-size: 16px;
      color: var(--ink);
    }}
  </style>
</head>
<body>
  <section class=\"card\">
    <div class=\"hero\">
      <span class=\"kicker\" id=\"kicker\">{ui["kicker"]}</span>
      <h1 id=\"title\">{ui["title"]}</h1>
      <p class=\"subtitle\" id=\"subtitle\">{ui["subtitle"]}</p>
    </div>

    <section class=\"section\" id=\"identitySection\">
      <div class=\"grid two\">
        <label>
          <span id=\"nameLabel\">{ui["name_label"]}</span>
          <input id=\"patientName\" maxlength=\"120\" required />
        </label>
        <label>
          <span id=\"phoneLabel\">{ui["phone_label"]}</span>
          <input id=\"phoneNumber\" maxlength=\"20\" required />
        </label>
      </div>
      <div class=\"booking-for\">
        <span id=\"bookingForLabel\">{ui["booking_for_label"]}</span>
        <div class=\"booking-for-group\">
          <label class=\"booking-for-option\">
            <input type=\"radio\" name=\"bookingFor\" value=\"self\" checked />
            <span id=\"bookingForSelf\">{ui["booking_for_self"]}</span>
          </label>
          <label class=\"booking-for-option\">
            <input type=\"radio\" name=\"bookingFor\" value=\"other\" />
            <span id=\"bookingForOther\">{ui["booking_for_other"]}</span>
          </label>
        </div>
      </div>
      <button id=\"continueBtn\" type=\"button\">{ui["continue"]}</button>
      <div id=\"statusLine\" class=\"status-line\"></div>
    </section>

    <section class=\"section hidden\" id=\"existingSection\">
      <h2 class=\"section-title\" id=\"manageTitle\">{ui["manage_title"]}</h2>
      <p class=\"section-subtitle\" id=\"manageSubtitle\"></p>
      <div id=\"appointmentList\" class=\"appt-list\"></div>
      <button id=\"bookInsteadBtn\" type=\"button\" class=\"alt\">Book New Instead</button>
    </section>

    <section class=\"section hidden\" id=\"bookingSection\">
      <h2 class=\"section-title\" id=\"bookingTitle\">{ui["booking_title"]}</h2>
      <div class=\"grid three\">
        <label>
          <span id=\"clinicLabel\">{ui["clinic_label"]}</span>
          <select id=\"clinicSelect\"></select>
        </label>
        <label>
          <span id=\"dateLabel\">{ui["date_label"]}</span>
          <select id=\"dateSelect\"></select>
        </label>
        <label id=\"periodWrap\" class=\"field-wrap hidden\">
          <span id=\"periodLabel\">{ui["period_label"]}</span>
          <select id=\"periodSelect\"></select>
        </label>
        <label>
          <span id=\"timeLabel\">{ui["time_label"]}</span>
          <select id=\"timeSelect\"></select>
        </label>
      </div>
      <button id=\"bookBtn\" type=\"button\">{ui["book_submit"]}</button>
    </section>

    <section class=\"section hidden\" id=\"rescheduleSection\">
      <h2 class=\"section-title\" id=\"rescheduleTitle\">{ui["reschedule_title"]}</h2>
      <div class=\"grid three\">
        <label>
          <span id=\"rescheduleClinicLabel\">{ui["clinic_label"]}</span>
          <select id=\"rescheduleClinicSelect\"></select>
        </label>
        <label>
          <span id=\"rescheduleDateLabel\">{ui["date_label"]}</span>
          <select id=\"rescheduleDateSelect\"></select>
        </label>
        <label id=\"reschedulePeriodWrap\" class=\"field-wrap hidden\">
          <span id=\"reschedulePeriodLabel\">{ui["period_label"]}</span>
          <select id=\"reschedulePeriodSelect\"></select>
        </label>
        <label>
          <span id=\"rescheduleTimeLabel\">{ui["time_label"]}</span>
          <select id=\"rescheduleTimeSelect\"></select>
        </label>
      </div>
      <button id=\"rescheduleBtn\" type=\"button\">{ui["reschedule_submit"]}</button>
    </section>
  </section>

  <div id=\"resultModal\" class=\"modal\" aria-hidden=\"true\">
    <div class=\"modal-card\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"modalTitle\">
      <div id=\"modalTitle\" class=\"modal-head\">{ui["modal_err"]}</div>
      <div id=\"modalBody\" class=\"modal-body\"></div>
      <div class=\"modal-actions\">
        <button id=\"modalCloseBtn\" type=\"button\">{ui["close"]}</button>
      </div>
    </div>
  </div>

  <div id=\"confirmModal\" class=\"modal\" aria-hidden=\"true\">
    <div class=\"modal-card\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"confirmTitle\">
      <div id=\"confirmTitle\" class=\"modal-head warn\">{ui["modal_warn"]}</div>
      <div id=\"confirmBody\" class=\"modal-body\"></div>
      <div class=\"modal-actions\">
        <button id=\"confirmCancelBtn\" type=\"button\" class=\"alt\">{ui["close"]}</button>
        <button id=\"confirmOkBtn\" type=\"button\" class=\"danger\">{ui["close"]}</button>
      </div>
    </div>
  </div>

  <script>
    const t = {texts_json};
    const serverLang = "{lang}";
    const lockLanguage = {"true" if lock_language else "false"};
    const doctorId = {int(doctor_id)};
    let activeLanguage = serverLang;
    let activeAppointments = [];
    let clinicOptions = [];
    let pendingRescheduleId = null;
    let confirmAction = null;

    function currentTexts() {{
      return t[activeLanguage] || t.en;
    }}

    function applyLanguage(lang) {{
      activeLanguage = lang in t ? lang : "en";
      const d = currentTexts();
      document.getElementById("kicker").textContent = d.kicker;
      document.getElementById("title").textContent = d.title;
      document.getElementById("subtitle").textContent = d.subtitle;
      document.getElementById("nameLabel").textContent = d.nameLabel;
      document.getElementById("phoneLabel").textContent = d.phoneLabel;
      document.getElementById("bookingForLabel").textContent = d.bookingForLabel;
      document.getElementById("bookingForSelf").textContent = d.bookingForSelf;
      document.getElementById("bookingForOther").textContent = d.bookingForOther;
      document.getElementById("continueBtn").textContent = d.continue;
      document.getElementById("manageTitle").textContent = d.manageTitle;
      document.getElementById("manageSubtitle").textContent = d.existingHint;
      document.getElementById("bookInsteadBtn").textContent = d.backToBooking;
      document.getElementById("bookingTitle").textContent = d.bookingTitle;
      document.getElementById("clinicLabel").textContent = d.clinicLabel;
      document.getElementById("dateLabel").textContent = d.dateLabel;
      document.getElementById("periodLabel").textContent = d.periodLabel;
      document.getElementById("timeLabel").textContent = d.timeLabel;
      document.getElementById("bookBtn").textContent = d.bookSubmit;
      document.getElementById("rescheduleTitle").textContent = d.rescheduleTitle;
      document.getElementById("rescheduleClinicLabel").textContent = d.clinicLabel;
      document.getElementById("rescheduleDateLabel").textContent = d.dateLabel;
      document.getElementById("reschedulePeriodLabel").textContent = d.periodLabel;
      document.getElementById("rescheduleTimeLabel").textContent = d.timeLabel;
      document.getElementById("rescheduleBtn").textContent = d.rescheduleSubmit;
      document.getElementById("modalCloseBtn").textContent = d.close;
      document.getElementById("confirmTitle").textContent = d.confirmCancelTitle;
      document.getElementById("confirmCancelBtn").textContent = d.confirmNo;
      document.getElementById("confirmOkBtn").textContent = d.confirmYes;
    }}

    function selectedBookingForSelf() {{
      return (document.querySelector('input[name="bookingFor"]:checked')?.value || "self") === "self";
    }}

    function toggleBookInsteadVisibility() {{
      const button = document.getElementById("bookInsteadBtn");
      button.classList.toggle("hidden", selectedBookingForSelf());
    }}

    function togglePeriodVisibility(periodWrap, enabled) {{
      periodWrap.classList.toggle("hidden", !enabled);
    }}

    function setStatus(message) {{
      const box = document.getElementById("statusLine");
      box.textContent = message || "";
      box.classList.toggle("visible", Boolean(message));
    }}

    function escapeHtml(value) {{
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }}

    function summaryHtml(summary) {{
      const texts = currentTexts();
      const lines = [];
      if (summary?.appointmentId) {{
        lines.push(`<div class="result-row"><strong>${{texts.appointmentIdPrefix}}:</strong> ${{escapeHtml(summary.appointmentId)}}</div>`);
      }}
      if (summary?.clinic) {{
        lines.push(`<div class="result-row"><strong>${{texts.clinicPrefix}}:</strong> ${{escapeHtml(summary.clinic)}}</div>`);
      }}
      if (summary?.date) {{
        lines.push(`<div class="result-row"><strong>${{texts.datePrefix}}:</strong> ${{escapeHtml(summary.date)}}</div>`);
      }}
      if (summary?.time) {{
        lines.push(`<div class="result-row"><strong>${{texts.timePrefix}}:</strong> ${{escapeHtml(summary.time)}}</div>`);
      }}
      return lines.length ? `<div class="result-summary">${{lines.join("")}}</div>` : "";
    }}

    function resultModalTitle(kind, message) {{
      const texts = currentTexts();
      const lower = String(message || "").toLowerCase();
      if (kind === "ok") {{
        if (lower.includes("cancel")) return texts.modalCancelled || texts.modalOk;
        if (lower.includes("reschedule")) return texts.modalRescheduled || texts.modalOk;
        return texts.modalBooked || texts.modalOk;
      }}
      if (kind === "warn") return texts.modalWarn;
      return texts.modalErr;
    }}

    function formatResultHtml(message, summary) {{
      const messageHtml = String(message || "")
        .split("\\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .filter((line) => {{
          const lower = line.toLowerCase();
          if (summary?.appointmentId && lower.startsWith("appointment id:")) return false;
          if (summary?.clinic && lower.startsWith("clinic:")) return false;
          if (summary?.date && lower.startsWith("date:")) return false;
          if (summary?.time && lower.startsWith("time:")) return false;
          return true;
        }})
        .map((line) => `<div class="modal-note">${{escapeHtml(line)}}</div>`)
        .join("");
      return `<div class="result-lines">${{messageHtml}}${{summaryHtml(summary)}}</div>`;
    }}

    function openResultModal(kind, message, summary) {{
      const title = document.getElementById("modalTitle");
      title.className = "modal-head " + kind;
      title.textContent = resultModalTitle(kind, message);
      document.getElementById("modalBody").innerHTML = formatResultHtml(message, summary);
      const modal = document.getElementById("resultModal");
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }}

    function closeResultModal() {{
      const modal = document.getElementById("resultModal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }}

    function openConfirmModal(message, onConfirm) {{
      confirmAction = onConfirm || null;
      document.getElementById("confirmBody").innerHTML = `<div class="modal-note">${{escapeHtml(message)}}</div>`;
      const modal = document.getElementById("confirmModal");
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }}

    function closeConfirmModal() {{
      confirmAction = null;
      const modal = document.getElementById("confirmModal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }}

    function hideSections() {{
      document.getElementById("existingSection").classList.add("hidden");
      document.getElementById("bookingSection").classList.add("hidden");
      document.getElementById("rescheduleSection").classList.add("hidden");
    }}

    function renderOptions(selectEl, items, emptyLabel) {{
      selectEl.innerHTML = "";
      if (!items.length) {{
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = emptyLabel;
        selectEl.appendChild(opt);
        return;
      }}
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "--";
      selectEl.appendChild(placeholder);
      items.forEach((item) => {{
        const opt = document.createElement("option");
        opt.value = item.value;
        opt.textContent = item.label;
        selectEl.appendChild(opt);
      }});
    }}

    function appointmentLine(row) {{
      const texts = currentTexts();
      const appointmentNumber = row.booking_number ?? row.appointment_id;
      return `
        <div class="appt-card">
          <div class="appt-meta">
            <div><strong>${{texts.appointmentIdPrefix}}:</strong> ${{appointmentNumber}}</div>
            <div><strong>${{texts.clinicPrefix}}:</strong> ${{row.clinic_name || "-"}}</div>
            <div><strong>${{texts.datePrefix}}:</strong> ${{row.slot_date || "-"}}</div>
            <div><strong>${{texts.timePrefix}}:</strong> ${{row.slot_time || "-"}}</div>
          </div>
          <div class="appt-actions">
            <button type="button" data-action="reschedule" data-id="${{row.appointment_id}}">${{texts.rescheduleBtn}}</button>
            <button type="button" class="danger" data-action="cancel" data-id="${{row.appointment_id}}">${{texts.cancelBtn}}</button>
          </div>
        </div>
      `;
    }}

    function renderExisting(rows) {{
      activeAppointments = rows || [];
      document.getElementById("appointmentList").innerHTML = activeAppointments.map(appointmentLine).join("");
      toggleBookInsteadVisibility();
      document.getElementById("existingSection").classList.remove("hidden");
      document.getElementById("bookingSection").classList.add("hidden");
      document.getElementById("rescheduleSection").classList.add("hidden");
    }}

    async function fetchJson(url, options) {{
      const resp = await fetch(url, options);
      let data = null;
      try {{
        data = await resp.json();
      }} catch (_err) {{
        data = null;
      }}
      if (!resp.ok) {{
        throw new Error((data && (data.detail || data.message)) || currentTexts().requestFailed);
      }}
      return data || {{}};
    }}

    async function loadClinics(targetSelect) {{
      const data = await fetchJson(`/whatsapp/web/clinics?doctor_id=${{doctorId}}&lang=${{encodeURIComponent(activeLanguage)}}`);
      clinicOptions = Array.isArray(data.clinics) ? data.clinics : [];
        renderOptions(
        targetSelect,
        clinicOptions.map((row) => ({{
          value: String(row.clinic_id),
          label: row.location ? `${{row.clinic_name}} (${{row.location}})` : row.clinic_name,
        }})),
        currentTexts().noClinics,
      );
    }}

    async function loadDates(clinicId, targetSelect) {{
      renderOptions(targetSelect, [], currentTexts().loadingDates);
      const data = await fetchJson(`/whatsapp/web/dates?doctor_id=${{doctorId}}&clinic_id=${{encodeURIComponent(clinicId)}}&lang=${{encodeURIComponent(activeLanguage)}}`);
      const dates = Array.isArray(data.dates) ? data.dates : [];
      renderOptions(targetSelect, dates.map((value) => ({{ value, label: value }})), currentTexts().noDates);
    }}

    async function loadTimes(clinicId, slotDate, period, periodSelect, targetSelect) {{
      const periodWrap = periodSelect.id === "periodSelect"
        ? document.getElementById("periodWrap")
        : document.getElementById("reschedulePeriodWrap");
      renderOptions(periodSelect, [], currentTexts().loadingPeriods);
      periodSelect.disabled = true;
      togglePeriodVisibility(periodWrap, false);
      renderOptions(targetSelect, [], currentTexts().loadingTimes);
      const periodQuery = period ? `&period=${{encodeURIComponent(period)}}` : "";
      const data = await fetchJson(`/whatsapp/web/times?doctor_id=${{doctorId}}&clinic_id=${{encodeURIComponent(clinicId)}}&slot_date=${{encodeURIComponent(slotDate)}}&lang=${{encodeURIComponent(activeLanguage)}}${{periodQuery}}`);
      if (data.mode === "periods") {{
        const periods = Array.isArray(data.periods) ? data.periods : [];
        renderOptions(periodSelect, periods, currentTexts().noTimes);
        periodSelect.disabled = false;
        togglePeriodVisibility(periodWrap, true);
        renderOptions(targetSelect, [], currentTexts().noTimes);
        return;
      }}
      renderOptions(periodSelect, [{{ value: "", label: "--" }}], "--");
      const slots = Array.isArray(data.slots) ? data.slots : [];
      renderOptions(targetSelect, slots, currentTexts().noTimes);
    }}

    async function showBookingSection() {{
      hideSections();
      document.getElementById("bookingSection").classList.remove("hidden");
      const clinicSelect = document.getElementById("clinicSelect");
      const dateSelect = document.getElementById("dateSelect");
      const periodSelect = document.getElementById("periodSelect");
      const timeSelect = document.getElementById("timeSelect");
      await loadClinics(clinicSelect);
      renderOptions(dateSelect, [], currentTexts().noDates);
      renderOptions(periodSelect, [], currentTexts().noTimes);
      togglePeriodVisibility(document.getElementById("periodWrap"), false);
      renderOptions(timeSelect, [], currentTexts().noTimes);
    }}

    async function showRescheduleSection(appointmentId) {{
      pendingRescheduleId = appointmentId;
      hideSections();
      document.getElementById("rescheduleSection").classList.remove("hidden");
      const clinicSelect = document.getElementById("rescheduleClinicSelect");
      const dateSelect = document.getElementById("rescheduleDateSelect");
      const periodSelect = document.getElementById("reschedulePeriodSelect");
      const timeSelect = document.getElementById("rescheduleTimeSelect");
      await loadClinics(clinicSelect);
      renderOptions(dateSelect, [], currentTexts().noDates);
      renderOptions(periodSelect, [], currentTexts().noTimes);
      togglePeriodVisibility(document.getElementById("reschedulePeriodWrap"), false);
      renderOptions(timeSelect, [], currentTexts().noTimes);
    }}

    async function doLookup() {{
      const name = document.getElementById("patientName").value.trim();
      const phone = document.getElementById("phoneNumber").value.trim();
      if (!doctorId) {{
        setStatus(currentTexts().missingDoctor);
        return;
      }}
      if (!name || !phone) {{
        setStatus(currentTexts().missingIdentity);
        return;
      }}
      setStatus(currentTexts().continueLoading);
      try {{
        const data = await fetchJson("/whatsapp/web/lookup", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            doctor_id: doctorId,
            patient_name: name,
            phone_number: phone,
            detected_language: activeLanguage,
          }}),
        }});
        setStatus("");
        if (Array.isArray(data.appointments) && data.appointments.length) {{
          renderExisting(data.appointments);
          return;
        }}
        await showBookingSection();
      }} catch (err) {{
        setStatus(String(err.message || currentTexts().requestFailed));
      }}
    }}

    async function submitBooking() {{
      const clinicId = document.getElementById("clinicSelect").value;
      const slotDate = document.getElementById("dateSelect").value;
      const slotTime = document.getElementById("timeSelect").value;
      const name = document.getElementById("patientName").value.trim();
      const phone = document.getElementById("phoneNumber").value.trim();
      if (!clinicId || !slotDate || !slotTime) {{
        openResultModal("err", currentTexts().missingBookingFields);
        return;
      }}
      try {{
        const clinicLabel = document.getElementById("clinicSelect").selectedOptions[0]?.textContent || "";
        const slotLabel = document.getElementById("timeSelect").selectedOptions[0]?.textContent || slotTime;
        const data = await fetchJson("/whatsapp/web/book", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            doctor_id: doctorId,
            clinic_id: Number(clinicId),
            appointment_date: slotDate,
            appointment_time: slotTime,
            patient_name: name,
            phone_number: phone,
            booking_for_self: selectedBookingForSelf(),
            detected_language: activeLanguage,
          }}),
        }});
        const kind = data.status === "booked" ? "ok" : data.status === "active_booking" ? "warn" : "err";
        openResultModal(kind, data.message || "", {{
          appointmentId: data.booking_number || data.appointment_id || "",
          clinic: clinicLabel,
          date: slotDate,
          time: slotLabel,
        }});
      }} catch (err) {{
        openResultModal("err", String(err.message || currentTexts().submitFailed));
      }}
    }}

    async function submitReschedule() {{
      const clinicId = document.getElementById("rescheduleClinicSelect").value;
      const slotDate = document.getElementById("rescheduleDateSelect").value;
      const slotTime = document.getElementById("rescheduleTimeSelect").value;
      if (!pendingRescheduleId || !clinicId || !slotDate || !slotTime) {{
        openResultModal("err", currentTexts().missingBookingFields);
        return;
      }}
      try {{
        const clinicLabel = document.getElementById("rescheduleClinicSelect").selectedOptions[0]?.textContent || "";
        const slotLabel = document.getElementById("rescheduleTimeSelect").selectedOptions[0]?.textContent || slotTime;
        const data = await fetchJson("/whatsapp/web/reschedule", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            doctor_id: doctorId,
            appointment_id: Number(pendingRescheduleId),
            clinic_id: Number(clinicId),
            appointment_date: slotDate,
            appointment_time: slotTime,
            detected_language: activeLanguage,
          }}),
        }});
        openResultModal(data.status === "ok" ? "ok" : "err", data.message || "", {{
          appointmentId: data.booking_number || data.appointment_id || pendingRescheduleId || "",
          clinic: clinicLabel,
          date: slotDate,
          time: slotLabel,
        }});
      }} catch (err) {{
        openResultModal("err", String(err.message || currentTexts().submitFailed));
      }}
    }}

    async function cancelAppointment(appointmentId) {{
      const row = activeAppointments.find((item) => String(item.appointment_id) === String(appointmentId)) || null;
      openConfirmModal(currentTexts().confirmCancel, async () => {{
        try {{
          const data = await fetchJson("/whatsapp/web/cancel", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{
              doctor_id: doctorId,
              appointment_id: Number(appointmentId),
              detected_language: activeLanguage,
            }}),
          }});
          closeConfirmModal();
          openResultModal(data.status === "ok" ? "ok" : "err", data.message || "", {{
            appointmentId: row?.booking_number || row?.appointment_id || appointmentId,
            clinic: row?.clinic_name || "",
            date: row?.slot_date || "",
            time: row?.slot_time || "",
          }});
          await doLookup();
        }} catch (err) {{
          closeConfirmModal();
          openResultModal("err", String(err.message || currentTexts().submitFailed));
        }}
      }});
    }}

    document.getElementById("continueBtn").addEventListener("click", doLookup);
    document.getElementById("bookBtn").addEventListener("click", submitBooking);
    document.getElementById("rescheduleBtn").addEventListener("click", submitReschedule);
    document.getElementById("bookInsteadBtn").addEventListener("click", async () => {{
      if (selectedBookingForSelf()) {{
        openResultModal("warn", currentTexts().bookInsteadBlocked);
        return;
      }}
      await showBookingSection();
    }});
    document.getElementById("modalCloseBtn").addEventListener("click", closeResultModal);
    document.getElementById("resultModal").addEventListener("click", (e) => {{
      if (e.target.id === "resultModal") closeResultModal();
    }});
    document.getElementById("confirmCancelBtn").addEventListener("click", closeConfirmModal);
    document.getElementById("confirmOkBtn").addEventListener("click", async () => {{
      const action = confirmAction;
      if (!action) {{
        closeConfirmModal();
        return;
      }}
      await action();
    }});
    document.getElementById("confirmModal").addEventListener("click", (e) => {{
      if (e.target.id === "confirmModal") closeConfirmModal();
    }});

    document.getElementById("appointmentList").addEventListener("click", async (e) => {{
      const action = e.target.getAttribute("data-action");
      const appointmentId = e.target.getAttribute("data-id");
      if (!action || !appointmentId) return;
      if (action === "cancel") {{
        await cancelAppointment(appointmentId);
      }} else if (action === "reschedule") {{
        await showRescheduleSection(appointmentId);
      }}
    }});

    document.getElementById("clinicSelect").addEventListener("change", async (e) => {{
      const clinicId = e.target.value;
      renderOptions(document.getElementById("periodSelect"), [], currentTexts().noTimes);
      togglePeriodVisibility(document.getElementById("periodWrap"), false);
      renderOptions(document.getElementById("timeSelect"), [], currentTexts().noTimes);
      if (clinicId) {{
        await loadDates(clinicId, document.getElementById("dateSelect"));
      }}
    }});
    document.getElementById("dateSelect").addEventListener("change", async (e) => {{
      const clinicId = document.getElementById("clinicSelect").value;
      const slotDate = e.target.value;
      if (clinicId && slotDate) {{
        await loadTimes(
          clinicId,
          slotDate,
          "",
          document.getElementById("periodSelect"),
          document.getElementById("timeSelect"),
        );
      }}
    }});
    document.getElementById("periodSelect").addEventListener("change", async (e) => {{
      const clinicId = document.getElementById("clinicSelect").value;
      const slotDate = document.getElementById("dateSelect").value;
      const period = e.target.value;
      if (clinicId && slotDate && period) {{
        await loadTimes(
          clinicId,
          slotDate,
          period,
          document.getElementById("periodSelect"),
          document.getElementById("timeSelect"),
        );
      }}
    }});
    document.getElementById("rescheduleClinicSelect").addEventListener("change", async (e) => {{
      const clinicId = e.target.value;
      renderOptions(document.getElementById("reschedulePeriodSelect"), [], currentTexts().noTimes);
      togglePeriodVisibility(document.getElementById("reschedulePeriodWrap"), false);
      renderOptions(document.getElementById("rescheduleTimeSelect"), [], currentTexts().noTimes);
      if (clinicId) {{
        await loadDates(clinicId, document.getElementById("rescheduleDateSelect"));
      }}
    }});
    document.getElementById("rescheduleDateSelect").addEventListener("change", async (e) => {{
      const clinicId = document.getElementById("rescheduleClinicSelect").value;
      const slotDate = e.target.value;
      if (clinicId && slotDate) {{
        await loadTimes(
          clinicId,
          slotDate,
          "",
          document.getElementById("reschedulePeriodSelect"),
          document.getElementById("rescheduleTimeSelect"),
        );
      }}
    }});
    document.getElementById("reschedulePeriodSelect").addEventListener("change", async (e) => {{
      const clinicId = document.getElementById("rescheduleClinicSelect").value;
      const slotDate = document.getElementById("rescheduleDateSelect").value;
      const period = e.target.value;
      if (clinicId && slotDate && period) {{
        await loadTimes(
          clinicId,
          slotDate,
          period,
          document.getElementById("reschedulePeriodSelect"),
          document.getElementById("rescheduleTimeSelect"),
        );
      }}
    }});

    document.querySelectorAll('input[name="bookingFor"]').forEach((radio) => {{
      radio.addEventListener("change", () => {{
        toggleBookInsteadVisibility();
      }});
    }});

    applyLanguage(serverLang);
    toggleBookInsteadVisibility();
  </script>
</body>
</html>"""
