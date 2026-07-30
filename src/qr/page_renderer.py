import base64
import html as html_escape
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _dapto_logo_src() -> str:
    logo_path = Path(__file__).resolve().parents[2] / "Dapto_logo.jpeg"
    try:
        return "data:image/jpeg;base64," + base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def render_qr_page_html(
    *,
    doctor_id: int,
    clinic_id: int,
    doctor_name: str,
    clinic_name: str,
    result_message: str | None = None,
    result_status: str | None = None,
    patient_name: str = "",
    phone_number: str = "",
    language: str = "en",
    lock_language: bool = False,
) -> str:
    dapto_logo_src = _dapto_logo_src()
    doctor_name_safe = html_escape.escape(doctor_name or "Doctor")
    clinic_name_safe = html_escape.escape(clinic_name or "Clinic")
    patient_name_safe = html_escape.escape(patient_name or "")
    phone_number_safe = html_escape.escape(phone_number or "")
    result_message_safe = html_escape.escape(result_message or "")
    lang = language if language in {"en", "hi", "hinglish"} else "en"
    ui = {
        "en": {
            "kicker": "Book Your Appointment",
            "title": f"Welcome to Dr. {doctor_name_safe} clinic",
            "subtitle": f"Clinic: {clinic_name_safe}",
            "name_label": "Full Name",
            "phone_label": "Phone Number",
            "submit": "Submit",
            "modal_close": "Close",
            "modal_ok": "Appointment Confirmed",
            "modal_warn": "Booking Update",
            "modal_err": "Booking Status",
        },
        "hi": {
            "kicker": "अपॉइंटमेंट बुक करें",
            "title": f"Dr. {doctor_name_safe} क्लिनिक में आपका स्वागत है",
            "subtitle": f"क्लिनिक: {clinic_name_safe}",
            "name_label": "पूरा नाम",
            "phone_label": "फोन नंबर",
            "submit": "सबमिट करें",
            "modal_close": "बंद करें",
            "modal_ok": "अपॉइंटमेंट कन्फर्म हुई",
            "modal_warn": "बुकिंग अपडेट",
            "modal_err": "बुकिंग स्थिति",
        },
        "hinglish": {
            "kicker": "Book Your Appointment",
            "title": f"Dr. {doctor_name_safe} clinic mein aapka swagat hai",
            "subtitle": f"Clinic: {clinic_name_safe}",
            "name_label": "Full Name",
            "phone_label": "Phone Number",
            "submit": "Submit kariye",
            "modal_close": "Close",
            "modal_ok": "Appointment Confirmed",
            "modal_warn": "Booking Update",
            "modal_err": "Booking Status",
        },
    }[lang]
    result_class = ""
    if result_status == "booked":
        result_class = " ok"
    elif result_status in {"overflow", "active_booking"}:
        result_class = " warn"
    elif result_status:
        result_class = " err"
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Book Your Appointment</title>
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
      width: min(760px, 100%);
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
    .form-wrap {{
      padding: 20px 24px 24px 24px;
      display: grid;
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-weight: 600;
      font-size: 14px;
    }}
    input {{
      width: 100%;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 12px 12px;
      font-size: 15px;
      outline: none;
      transition: border-color .2s, box-shadow .2s;
    }}
    input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15,118,110,0.13);
    }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    @media (min-width: 640px) {{
      .grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    button {{
      margin-top: 4px;
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      color: white;
      background: linear-gradient(135deg, #0f766e, #0b5a53);
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{
      opacity: .7;
      cursor: not-allowed;
    }}
    .result {{
      border: 1px solid #dce9e2;
      background: #f8fcfa;
      border-radius: 12px;
      padding: 12px;
      min-height: 50px;
      font-size: 14px;
      white-space: pre-wrap;
    }}
    .ok {{ color: var(--ok); }}
    .result.ok {{ font-weight: 600; }}
    .warn {{ color: var(--warn); }}
    .err {{ color: var(--danger); }}
    .result {{
      display: none;
    }}
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
    .modal.open {{
      display: flex;
    }}
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
      white-space: normal;
      line-height: 1.55;
      font-size: 15px;
    }}
    .modal-actions {{
      padding: 0 20px 20px 20px;
      display: flex;
      justify-content: flex-end;
    }}
    .modal-actions button {{
      margin-top: 0;
      min-width: 120px;
    }}
    .brand-footer {{
      padding: 0 24px 24px 24px;
      display: grid;
      justify-items: center;
      gap: 8px;
      text-align: center;
      color: var(--muted);
    }}
    .brand-footer span {{
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .brand-footer img {{
      display: block;
      width: min(160px, 42vw);
      height: auto;
      object-fit: contain;
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
    <form class=\"form-wrap\" id=\"checkinForm\" method=\"post\" action=\"/qr/checkin/submit?doctor_id={doctor_id}&clinic_id={clinic_id}\">
      <div class=\"grid\">
        <label>
          <span id=\"nameLabel\">{ui["name_label"]}</span>
          <input id=\"patientName\" name=\"patient_name\" maxlength=\"120\" value=\"{patient_name_safe}\" required />
        </label>
        <label>
          <span id=\"phoneLabel\">{ui["phone_label"]}</span>
          <input id=\"phoneNumber\" name=\"phone_number\" maxlength=\"20\" value=\"{phone_number_safe}\" required />
        </label>
      </div>
      <button id=\"submitBtn\" type=\"submit\">{ui["submit"]}</button>
      <div id=\"result\" class=\"result{result_class}\">{result_message_safe}</div>
      <input type=\"hidden\" id=\"doctorId\" value=\"{doctor_id}\" />
      <input type=\"hidden\" id=\"clinicId\" value=\"{clinic_id}\" />
    </form>
    <div class=\"brand-footer\">
      <span>Powered by</span>
      <img src="{dapto_logo_src}" alt="" onerror="this.style.display='none'" />
    </div>
  </section>
  <div id=\"resultModal\" class=\"modal\" aria-hidden=\"true\">
    <div class=\"modal-card\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"modalTitle\">
      <div id=\"modalTitle\" class=\"modal-head\">{ui["modal_err"]}</div>
      <div id=\"modalBody\" class=\"modal-body\"></div>
      <div class=\"modal-actions\">
        <button id=\"modalCloseBtn\" type=\"button\">{ui["modal_close"]}</button>
      </div>
    </div>
  </div>

  <script>
    const t = {{
      en: {{
        kicker: "Book Your Appointment",
        title: "Welcome to Dr. {doctor_name_safe} clinic",
        subtitle: "Clinic: {clinic_name_safe}",
        nameLabel: "Full Name",
        phoneLabel: "Phone Number",
        submit: "Submit",
        close: "Close",
        modalOk: "Appointment Confirmed",
        modalWarn: "Booking Update",
        modalErr: "Booking Status",
        submitting: "Submitting...",
        missingIds: "Error: Doctor or clinic ID missing.",
        serverError: "Server error. Please try again.",
        requestFailed: "Request failed.",
        done: "Done.",
        submitFailed: "Unable to submit right now. Please try again.",
      }},
      hi: {{
        kicker: "अपॉइंटमेंट बुक करें",
        title: "Dr. {doctor_name_safe} क्लिनिक में आपका स्वागत है",
        subtitle: "क्लिनिक: {clinic_name_safe}",
        nameLabel: "पूरा नाम",
        phoneLabel: "फोन नंबर",
        submit: "सबमिट करें",
        close: "बंद करें",
        modalOk: "अपॉइंटमेंट कन्फर्म हुई",
        modalWarn: "बुकिंग अपडेट",
        modalErr: "बुकिंग स्थिति",
        submitting: "सबमिट हो रहा है...",
        missingIds: "त्रुटि: डॉक्टर या क्लिनिक आईडी नहीं मिली।",
        serverError: "सर्वर त्रुटि। कृपया फिर से कोशिश करें।",
        requestFailed: "रिक्वेस्ट असफल रही।",
        done: "हो गया।",
        submitFailed: "अभी सबमिट नहीं हो पाया। कृपया फिर से कोशिश करें।",
      }},
      hinglish: {{
        kicker: "Book Your Appointment",
        title: "Dr. {doctor_name_safe} clinic mein aapka swagat hai",
        subtitle: "Clinic: {clinic_name_safe}",
        nameLabel: "Full Name",
        phoneLabel: "Phone Number",
        submit: "Submit kariye",
        close: "Close",
        modalOk: "Appointment Confirmed",
        modalWarn: "Booking Update",
        modalErr: "Booking Status",
        submitting: "Submit ho raha hai...",
        missingIds: "Error: Doctor ya clinic ID missing hai.",
        serverError: "Server error. Please dobara try kariye.",
        requestFailed: "Request fail hui.",
        done: "Ho gaya.",
        submitFailed: "Abhi submit nahi ho paya. Please dobara try kariye.",
      }},
    }};

    const serverLang = "{lang}";
    const lockLanguage = {"true" if lock_language else "false"};
    let activeLanguage = serverLang;

    function applyLanguage(lang) {{
      const d = t[lang] || t.en;
      activeLanguage = lang in t ? lang : "en";
      document.getElementById("kicker").textContent = d.kicker;
      document.getElementById("title").textContent = d.title;
      document.getElementById("subtitle").textContent = d.subtitle;
      document.getElementById("nameLabel").textContent = d.nameLabel;
      document.getElementById("phoneLabel").textContent = d.phoneLabel;
      document.getElementById("submitBtn").textContent = d.submit;
      document.getElementById("modalCloseBtn").textContent = d.close;
    }}

    function formatResultHtml(message, currentTexts, kind) {{
      const confirmationLines = [
        "Appointment confirmed.",
        "अपॉइंटमेंट कन्फर्म हो गई।",
        "Appointment confirm ho gayi.",
      ];
      const lines = String(message || currentTexts.done)
        .split("\\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .filter((line, index) => !(kind === "ok" && index === 0 && confirmationLines.includes(line)));
      const boldPrefixes = [
        "Appointment ID:",
        "अपॉइंटमेंट आईडी:",
        "Estimated Time:",
        "अनुमानित समय:",
      ];
      return lines
        .map((line) => {{
          const safe = line
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          const shouldBold = boldPrefixes.some((prefix) => safe.startsWith(prefix));
          return shouldBold ? "<strong>" + safe + "</strong>" : safe;
        }})
        .join("<br>");
    }}

    function openResultModal(kind, htmlMessage) {{
      const currentTexts = t[activeLanguage] || t.en;
      const title = document.getElementById("modalTitle");
      const body = document.getElementById("modalBody");
      const modal = document.getElementById("resultModal");
      title.className = "modal-head " + kind;
      title.textContent =
        kind === "ok" ? currentTexts.modalOk :
        kind === "warn" ? currentTexts.modalWarn :
        currentTexts.modalErr;
      body.innerHTML = htmlMessage;
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }}

    function closeResultModal() {{
      const modal = document.getElementById("resultModal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }}

    applyLanguage(serverLang);

    document.getElementById("modalCloseBtn").addEventListener("click", closeResultModal);
    document.getElementById("resultModal").addEventListener("click", (e) => {{
      if (e.target.id === "resultModal") closeResultModal();
    }});

    const initialResult = document.getElementById("result").textContent.trim();
    if (initialResult) {{
      openResultModal(
        "{'ok' if result_status == 'booked' else 'warn' if result_status in {'overflow', 'active_booking'} else 'err'}",
        formatResultHtml(
          initialResult,
          t[activeLanguage] || t.en,
          "{'ok' if result_status == 'booked' else 'warn' if result_status in {'overflow', 'active_booking'} else 'err'}"
        )
      );
    }}

    const checkinForm = document.getElementById("checkinForm");
    checkinForm.addEventListener("submit", async (e) => {{
      e.preventDefault();
      e.stopPropagation();
      const submitUrl = `/qr/checkin/submit?lang=${{encodeURIComponent(activeLanguage)}}`;
      const result = document.getElementById("result");
      const btn = document.getElementById("submitBtn");
      const currentTexts = t[activeLanguage] || t.en;
      btn.disabled = true;
      result.className = "result";
      result.textContent = currentTexts.submitting;
      try {{
        const payload = {{
          doctor_id: Number(document.getElementById("doctorId").value),
          clinic_id: Number(document.getElementById("clinicId").value),
          patient_name: document.getElementById("patientName").value,
          phone_number: document.getElementById("phoneNumber").value,
          detected_language: activeLanguage,
        }};
        if (!payload.doctor_id || !payload.clinic_id) {{
          result.classList.add("err");
          result.textContent = currentTexts.missingIds;
          btn.disabled = false;
          return;
        }}
        const resp = await fetch(submitUrl, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload),
        }});
        let data;
        try {{
          data = await resp.json();
        }} catch {{
          openResultModal("err", formatResultHtml(currentTexts.serverError, currentTexts, "err"));
          btn.disabled = false;
          return;
        }}
        if (!resp.ok) {{
          openResultModal("err", formatResultHtml(data.detail || data.message || currentTexts.requestFailed, currentTexts, "err"));
        }} else {{
          const status = data.status || "";
          const kind = status === "booked" ? "ok" : (status === "overflow" || status === "active_booking") ? "warn" : "err";
          openResultModal(kind, formatResultHtml(data.message || currentTexts.done, currentTexts, kind));
        }}
      }} catch (_err) {{
        openResultModal("err", formatResultHtml(currentTexts.submitFailed, currentTexts, "err"));
      }} finally {{
        btn.disabled = false;
      }}
    }}, true);
  </script>
</body>
</html>"""


def render_hospital_registration_page_html(
    *,
    hospital_code: str,
    hospital_name: str,
    doctors: list,
    specializations: list,
    language: str = "en",
) -> str:
    """Standalone hospital REGISTRATION page. Static doctor/specialization lists, no DB,
    and on submit it shows a generated unique token instead of booking an appointment."""
    dapto_logo_src = _dapto_logo_src()
    hospital_code_safe = html_escape.escape(hospital_code or "")
    hospital_name = str(hospital_name or "").strip() or "Hospital"
    hospital_name_safe = html_escape.escape(hospital_name)
    doctors_json = json.dumps(doctors or [], ensure_ascii=False)
    specializations_json = json.dumps(specializations or [], ensure_ascii=False)
    lang = language if language in {"en", "hi", "hinglish"} else "en"
    submit_text = {
        "en": "Submit",
        "hi": "सबमिट करें",
        "hinglish": "Submit kariye",
    }[lang]
    ui = {
        "en": {
            "kicker": "Book Your Registration",
            "title": f"Welcome to {hospital_name_safe}",
        },
        "hi": {
            "kicker": "अपना पंजीकरण करें",
            "title": f"{hospital_name_safe} में आपका स्वागत है",
        },
        "hinglish": {
            "kicker": "Book Your Registration",
            "title": f"{hospital_name_safe} mein aapka swagat hai",
        },
    }[lang]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{hospital_name_safe}</title>
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
      width: min(760px, 100%);
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
    h1 {{ margin: 12px 0 8px 0; font-size: 28px; line-height: 1.2; }}
    .subtitle {{ margin: 0; color: var(--muted); font-size: 15px; }}
    .form-wrap {{ padding: 20px 24px 24px 24px; display: grid; gap: 14px; }}
    label {{ display: grid; gap: 6px; font-weight: 600; font-size: 14px; }}
    input, select {{
      width: 100%;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 12px 12px;
      font-size: 15px;
      outline: none;
      background: #fff;
      transition: border-color .2s, box-shadow .2s;
    }}
    input:focus, select:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15,118,110,0.13);
    }}
    .grid {{ display: grid; gap: 12px; }}
    @media (min-width: 640px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
    button {{
      margin-top: 4px;
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      color: white;
      background: linear-gradient(135deg, #0f766e, #0b5a53);
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{ opacity: .7; cursor: not-allowed; }}
    .result {{ display: none; }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .err {{ color: var(--danger); }}
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
    .modal-body {{ padding: 18px 20px; line-height: 1.55; font-size: 15px; }}
    .modal-actions {{ padding: 0 20px 20px 20px; display: flex; justify-content: flex-end; }}
    .modal-actions button {{ margin-top: 0; min-width: 120px; }}
    .brand-footer {{
      padding: 0 24px 24px 24px;
      display: grid;
      justify-items: center;
      gap: 8px;
      text-align: center;
      color: var(--muted);
    }}
    .brand-footer span {{
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .brand-footer img {{
      display: block;
      width: min(160px, 42vw);
      height: auto;
      object-fit: contain;
    }}
  </style>
</head>
<body>
  <section class="card">
    <div class="hero">
      <span class="kicker">{ui["kicker"]}</span>
      <h1>{ui["title"]}</h1>
    </div>
    <form class="form-wrap" id="registrationForm">
      <div class="grid">
        <label>
          <span>Specialization</span>
          <select id="specializationSelect"></select>
        </label>
        <label>
          <span>Doctor</span>
          <select id="doctorSelect" required></select>
        </label>
      </div>
      <div class="grid">
        <label>
          <span>Full Name</span>
          <input id="patientName" maxlength="120" required />
        </label>
        <label>
          <span>Phone Number</span>
          <input id="phoneNumber" maxlength="20" required />
        </label>
      </div>
      <div class="grid">
        <label>
          <span>Age</span>
          <input id="patientAge" type="number" min="0" max="130" required />
        </label>
        <label>
          <span>Gender</span>
          <select id="patientGender" required>
            <option value="">Select gender</option>
            <option value="Male">Male</option>
            <option value="Female">Female</option>
            <option value="Other">Other</option>
          </select>
        </label>
      </div>
      <button id="submitBtn" type="submit">{submit_text}</button>
      <div id="result" class="result"></div>
      <input type="hidden" id="hospitalCode" value="{hospital_code_safe}" />
      <input type="hidden" id="hospitalName" value="{hospital_name_safe}" />
    </form>
    <div class="brand-footer">
      <span>Powered by</span>
      <img src="{dapto_logo_src}" alt="" onerror="this.style.display='none'" />
    </div>
  </section>
  <div id="resultModal" class="modal" aria-hidden="true">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <div id="modalTitle" class="modal-head">Registration Status</div>
      <div id="modalBody" class="modal-body"></div>
      <div class="modal-actions">
        <button id="modalCloseBtn" type="button">Close</button>
      </div>
    </div>
  </div>
  <script>
    const doctors = {doctors_json};
    const specializations = {specializations_json};
    const lang = "{lang}";
    const labels = {{
      allSpecializations: "All specializations",
      chooseDoctor: "Select doctor",
      noSpecializations: "No specializations available",
      noDoctors: "No doctors available",
      missingDoctor: "Please choose a doctor.",
      missingFields: "Please fill name, age, gender and phone number.",
      submitting: "Submitting...",
      submit: {json.dumps(submit_text, ensure_ascii=False)},
      failed: "Unable to submit right now. Please try again.",
      tokenLabel: "Your Registration Token",
      modalOk: "Registration Successful",
      modalWarn: "Registration Update",
      modalErr: "Registration Status",
    }};

    function fillSpecializations() {{
      const select = document.getElementById("specializationSelect");
      select.innerHTML = "";
      if (!doctors.length) {{
        const option = new Option(labels.noSpecializations, "");
        option.disabled = true;
        select.appendChild(option);
        select.disabled = true;
        return;
      }}
      select.disabled = false;
      select.appendChild(new Option(labels.allSpecializations, ""));
      specializations.forEach((name) => select.appendChild(new Option(name, name)));
    }}

    function fillDoctors() {{
      const specialization = document.getElementById("specializationSelect").value;
      const select = document.getElementById("doctorSelect");
      const submitBtn = document.getElementById("submitBtn");
      const previous = select.value;
      select.innerHTML = "";
      const visibleDoctors = doctors.filter((doctor) => !specialization || doctor.specialization === specialization);
      if (!visibleDoctors.length) {{
        const option = new Option(labels.noDoctors, "");
        option.disabled = true;
        select.appendChild(option);
        select.disabled = true;
        submitBtn.disabled = true;
        return;
      }}
      select.disabled = false;
      submitBtn.disabled = false;
      select.appendChild(new Option(labels.chooseDoctor, ""));
      visibleDoctors.forEach((doctor) => {{
        const label = doctor.specialization ? `${{doctor.doctor_name}} - ${{doctor.specialization}}` : doctor.doctor_name;
        select.appendChild(new Option(label, String(doctor.doctor_id)));
      }});
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    }}

    function syncSpecializationFromDoctor() {{
      const doctorId = Number(document.getElementById("doctorSelect").value || 0);
      const doctor = doctors.find((item) => Number(item.doctor_id) === doctorId);
      if (doctor && doctor.specialization) {{
        document.getElementById("specializationSelect").value = doctor.specialization;
        fillDoctors();
        document.getElementById("doctorSelect").value = String(doctor.doctor_id);
      }}
    }}

    function openResultModal(kind, htmlMessage) {{
      const title = document.getElementById("modalTitle");
      const body = document.getElementById("modalBody");
      const modal = document.getElementById("resultModal");
      title.className = "modal-head " + kind;
      title.textContent = kind === "ok" ? labels.modalOk : kind === "warn" ? labels.modalWarn : labels.modalErr;
      body.innerHTML = htmlMessage;
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }}

    function closeResultModal() {{
      const modal = document.getElementById("resultModal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }}

    function escapeHtml(value) {{
      return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }}

    fillSpecializations();
    fillDoctors();
    document.getElementById("specializationSelect").addEventListener("change", fillDoctors);
    document.getElementById("doctorSelect").addEventListener("change", syncSpecializationFromDoctor);
    document.getElementById("modalCloseBtn").addEventListener("click", closeResultModal);
    document.getElementById("resultModal").addEventListener("click", (e) => {{
      if (e.target.id === "resultModal") closeResultModal();
    }});

    document.getElementById("registrationForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const btn = document.getElementById("submitBtn");
      const doctorId = document.getElementById("doctorSelect").value;
      const doctorSelect = document.getElementById("doctorSelect");
      const name = document.getElementById("patientName").value.trim();
      const age = document.getElementById("patientAge").value.trim();
      const gender = document.getElementById("patientGender").value;
      const phone = document.getElementById("phoneNumber").value.trim();
      if (!doctorId) {{
        openResultModal("err", escapeHtml(labels.missingDoctor));
        return;
      }}
      if (!name || !age || !gender || !phone) {{
        openResultModal("err", escapeHtml(labels.missingFields));
        return;
      }}
      btn.disabled = true;
      btn.textContent = labels.submitting;
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 45000);
      try {{
        const resp = await fetch(`/qr/hospital/registration/submit?lang=${{encodeURIComponent(lang)}}`, {{
          method: "POST",
          signal: controller.signal,
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            hospital_code: document.getElementById("hospitalCode").value,
            hospital_name: document.getElementById("hospitalName").value,
            doctor_id: doctorId,
            doctor_name: (doctorSelect.selectedOptions[0] || {{}}).text || "",
            specialization: document.getElementById("specializationSelect").value,
            patient_name: name,
            age: age,
            gender: gender,
            phone_number: phone,
            detected_language: lang,
          }}),
        }});
        const data = await resp.json();
        const st = data && data.status;
        if (st === "registered" && data.token) {{
          openResultModal(
            "ok",
            "<div>" + escapeHtml(labels.tokenLabel) + "</div>" +
            "<div style=\\"font-size:24px;font-weight:800;letter-spacing:1px;margin-top:8px;color:var(--accent)\\">" +
            escapeHtml(data.token) + "</div>"
          );
        }} else if (st === "already_registered") {{
          openResultModal(
            "warn",
            "<div>" + escapeHtml(data.message || "") + "</div>" +
            (data.token
              ? "<div style=\\"font-size:22px;font-weight:800;letter-spacing:1px;margin-top:8px;color:var(--accent)\\">" + escapeHtml(data.token) + "</div>"
              : "")
          );
        }} else {{
          openResultModal("err", escapeHtml((data && (data.message || data.detail)) || labels.failed));
        }}
      }} catch (err) {{
        openResultModal("err", escapeHtml(labels.failed));
      }} finally {{
        window.clearTimeout(timeoutId);
        btn.disabled = false;
        btn.textContent = labels.submit;
      }}
    }});
  </script>
</body>
</html>"""


def render_hospital_qr_page_html(
    *,
    hospital_code: str,
    options: dict,
    result_message: str | None = None,
    result_status: str | None = None,
    patient_name: str = "",
    phone_number: str = "",
    language: str = "en",
    lock_language: bool = False,
) -> str:
    dapto_logo_src = _dapto_logo_src()
    hospital_code_safe = html_escape.escape(hospital_code or "")
    patient_name_safe = html_escape.escape(patient_name or "")
    phone_number_safe = html_escape.escape(phone_number or "")
    result_message_safe = html_escape.escape(result_message or "")
    doctors_json = json.dumps(options.get("doctors") or [], ensure_ascii=False)
    specializations_json = json.dumps(options.get("specializations") or [], ensure_ascii=False)
    hospital_name = str(options.get("hospital_name") or "").strip()
    hospital_name_safe = html_escape.escape(hospital_name or "Hospital")
    lang = language if language in {"en", "hi", "hinglish"} else "en"
    result_class = ""
    if result_status == "booked":
        result_class = " ok"
    elif result_status in {"overflow", "active_booking"}:
        result_class = " warn"
    elif result_status:
        result_class = " err"
    submit_text = {
        "en": "Submit",
        "hi": "सबमिट करें",
        "hinglish": "Submit kariye",
    }[lang]
    ui = {
        "en": {
            "kicker": "Book Your Appointment",
            "title": f"Welcome to {hospital_name_safe}",
        },
        "hi": {
            "kicker": "अपॉइंटमेंट बुक करें",
            "title": f"{hospital_name_safe} में आपका स्वागत है",
        },
        "hinglish": {
            "kicker": "Book Your Appointment",
            "title": f"{hospital_name_safe} mein aapka swagat hai",
        },
    }[lang]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{hospital_name_safe}</title>
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
      width: min(760px, 100%);
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
    h1 {{ margin: 12px 0 8px 0; font-size: 28px; line-height: 1.2; }}
    .subtitle {{ margin: 0; color: var(--muted); font-size: 15px; }}
    .form-wrap {{ padding: 20px 24px 24px 24px; display: grid; gap: 14px; }}
    label {{ display: grid; gap: 6px; font-weight: 600; font-size: 14px; }}
    input, select {{
      width: 100%;
      border: 1px solid #ceded5;
      border-radius: 12px;
      padding: 12px 12px;
      font-size: 15px;
      outline: none;
      background: #fff;
      transition: border-color .2s, box-shadow .2s;
    }}
    input:focus, select:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15,118,110,0.13);
    }}
    .grid {{ display: grid; gap: 12px; }}
    @media (min-width: 640px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
    button {{
      margin-top: 4px;
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      color: white;
      background: linear-gradient(135deg, #0f766e, #0b5a53);
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{ opacity: .7; cursor: not-allowed; }}
    .result {{ display: none; }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .err {{ color: var(--danger); }}
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
    .modal-body {{ padding: 18px 20px; line-height: 1.55; font-size: 15px; }}
    .modal-actions {{ padding: 0 20px 20px 20px; display: flex; justify-content: flex-end; }}
    .modal-actions button {{ margin-top: 0; min-width: 120px; }}
    .brand-footer {{
      padding: 0 24px 24px 24px;
      display: grid;
      justify-items: center;
      gap: 8px;
      text-align: center;
      color: var(--muted);
    }}
    .brand-footer span {{
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .brand-footer img {{
      display: block;
      width: min(160px, 42vw);
      height: auto;
      object-fit: contain;
    }}
  </style>
</head>
<body>
  <section class="card">
    <div class="hero">
      <span class="kicker">{ui["kicker"]}</span>
      <h1>{ui["title"]}</h1>
    </div>
    <form class="form-wrap" id="hospitalCheckinForm">
      <div id="noDoctorsNotice" role="alert" style="display:none; margin:0 0 4px 0; padding:14px 16px; border-radius:12px; background:#fdf4e6; border:1px solid #f0d9b5; color:var(--warn); font-size:15px; font-weight:600; line-height:1.5; text-align:center;"></div>
      <div class="grid" id="selectsGrid">
        <label>
          <span>Specialization</span>
          <select id="specializationSelect"></select>
        </label>
        <label>
          <span>Doctor</span>
          <select id="doctorSelect" required></select>
        </label>
      </div>
      <div class="grid">
        <label>
          <span>Full Name</span>
          <input id="patientName" maxlength="120" value="{patient_name_safe}" required />
        </label>
        <label>
          <span>Phone Number</span>
          <input id="phoneNumber" maxlength="20" value="{phone_number_safe}" required />
        </label>
      </div>
      <button id="submitBtn" type="submit">{submit_text}</button>
      <div id="result" class="result{result_class}">{result_message_safe}</div>
      <input type="hidden" id="hospitalCode" value="{hospital_code_safe}" />
    </form>
    <div class="brand-footer">
      <span>Powered by</span>
      <img src="{dapto_logo_src}" alt="" onerror="this.style.display='none'" />
    </div>
  </section>
  <div id="resultModal" class="modal" aria-hidden="true">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <div id="modalTitle" class="modal-head">Booking Status</div>
      <div id="modalBody" class="modal-body"></div>
      <div class="modal-actions">
        <button id="modalCloseBtn" type="button">Close</button>
      </div>
    </div>
  </div>
  <script>
    const doctors = {doctors_json};
    const specializations = {specializations_json};
    const lang = "{lang}";
    const labels = {{
      allSpecializations: "All specializations",
      chooseDoctor: "Select doctor",
      noSpecializations: "No active specializations available",
      noDoctors: "No active doctors available",
      missingDoctor: "Please choose a doctor.",
      submitting: "Submitting...",
      submit: {json.dumps(submit_text, ensure_ascii=False)},
      done: "Done.",
      failed: "Unable to submit right now. Please try again.",
      timedOut: "The booking is taking longer than expected. Please refresh or check with the front desk before submitting again.",
      modalOk: "Appointment Confirmed",
      modalWarn: "Booking Update",
      modalErr: "Booking Status",
    }};

    function fillSpecializations() {{
      const select = document.getElementById("specializationSelect");
      select.innerHTML = "";
      if (!doctors.length) {{
        const option = new Option(labels.noSpecializations, "");
        option.disabled = true;
        select.appendChild(option);
        select.disabled = true;
        return;
      }}
      select.disabled = false;
      select.appendChild(new Option(labels.allSpecializations, ""));
      specializations.forEach((name) => select.appendChild(new Option(name, name)));
    }}

    function fillDoctors() {{
      const specialization = document.getElementById("specializationSelect").value;
      const select = document.getElementById("doctorSelect");
      const submitBtn = document.getElementById("submitBtn");
      const previous = select.value;
      select.innerHTML = "";
      const visibleDoctors = doctors.filter((doctor) => !specialization || doctor.specialization === specialization);
      if (!visibleDoctors.length) {{
        const option = new Option(labels.noDoctors, "");
        option.disabled = true;
        select.appendChild(option);
        select.disabled = true;
        submitBtn.disabled = true;
        return;
      }}
      select.disabled = false;
      submitBtn.disabled = false;
      select.appendChild(new Option(labels.chooseDoctor, ""));
      visibleDoctors.forEach((doctor) => {{
        const label = doctor.specialization ? `${{doctor.doctor_name}} - ${{doctor.specialization}}` : doctor.doctor_name;
        select.appendChild(new Option(label, String(doctor.doctor_id)));
      }});
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    }}

    function syncSpecializationFromDoctor() {{
      const doctorId = Number(document.getElementById("doctorSelect").value || 0);
      const doctor = doctors.find((item) => Number(item.doctor_id) === doctorId);
      if (doctor && doctor.specialization) {{
        document.getElementById("specializationSelect").value = doctor.specialization;
        fillDoctors();
        document.getElementById("doctorSelect").value = String(doctor.doctor_id);
      }}
    }}

    function formatResultHtml(message, kind) {{
      const confirmationLines = ["Appointment confirmed.", "अपॉइंटमेंट कन्फर्म हो गई।", "Appointment confirm ho gayi."];
      const lines = String(message || labels.done)
        .split("\\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .filter((line, index) => !(kind === "ok" && index === 0 && confirmationLines.includes(line)));
      const boldPrefixes = ["Appointment ID:", "अपॉइंटमेंट आईडी:", "Estimated Time:", "अनुमानित समय:"];
      return lines
        .map((line) => {{
          const safe = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
          return boldPrefixes.some((prefix) => safe.startsWith(prefix)) ? "<strong>" + safe + "</strong>" : safe;
        }})
        .join("<br>");
    }}

    function openResultModal(kind, htmlMessage) {{
      const title = document.getElementById("modalTitle");
      const body = document.getElementById("modalBody");
      const modal = document.getElementById("resultModal");
      title.className = "modal-head " + kind;
      title.textContent = kind === "ok" ? labels.modalOk : kind === "warn" ? labels.modalWarn : labels.modalErr;
      body.innerHTML = htmlMessage;
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }}

    function closeResultModal() {{
      const modal = document.getElementById("resultModal");
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }}

    fillSpecializations();
    fillDoctors();
    if (!doctors.length) {{
      const noDoctorsMsg = (
        lang === "hi"
          ? "आज बुकिंग के लिए कोई डॉक्टर उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें या फ्रंट डेस्क से संपर्क करें।"
          : lang === "hinglish"
          ? "Aaj booking ke liye koi doctor available nahi hai. Kripya baad mein try karein ya front desk se sampark karein."
          : "No doctors are available for booking today. Please try again later or contact the front desk."
      );
      const notice = document.getElementById("noDoctorsNotice");
      notice.textContent = noDoctorsMsg;
      notice.style.display = "block";
      const selectsGrid = document.getElementById("selectsGrid");
      if (selectsGrid) selectsGrid.style.display = "none";
      document.getElementById("submitBtn").disabled = true;
    }}
    document.getElementById("specializationSelect").addEventListener("change", fillDoctors);
    document.getElementById("doctorSelect").addEventListener("change", syncSpecializationFromDoctor);
    document.getElementById("modalCloseBtn").addEventListener("click", closeResultModal);
    document.getElementById("resultModal").addEventListener("click", (e) => {{
      if (e.target.id === "resultModal") closeResultModal();
    }});

    const initialResult = document.getElementById("result").textContent.trim();
    if (initialResult) {{
      const kind = "{'ok' if result_status == 'booked' else 'warn' if result_status in {'overflow', 'active_booking'} else 'err'}";
      openResultModal(kind, formatResultHtml(initialResult, kind));
    }}

    document.getElementById("hospitalCheckinForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const btn = document.getElementById("submitBtn");
      const doctorId = Number(document.getElementById("doctorSelect").value || 0);
      if (!doctorId) {{
        openResultModal("err", formatResultHtml(labels.missingDoctor, "err"));
        return;
      }}
      btn.disabled = true;
      btn.textContent = labels.submitting;
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 45000);
      try {{
        const resp = await fetch(`/qr/hospital/checkin/submit?lang=${{encodeURIComponent(lang)}}`, {{
          method: "POST",
          signal: controller.signal,
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            hospital_code: document.getElementById("hospitalCode").value,
            doctor_id: doctorId,
            specialization: document.getElementById("specializationSelect").value,
            patient_name: document.getElementById("patientName").value,
            phone_number: document.getElementById("phoneNumber").value,
            detected_language: lang,
          }}),
        }});
        const data = await resp.json();
        const status = data.status || "";
        const kind = status === "booked" ? "ok" : (status === "overflow" || status === "active_booking") ? "warn" : "err";
        openResultModal(kind, formatResultHtml(data.message || data.detail || labels.done, kind));
      }} catch (err) {{
        const message = err && err.name === "AbortError" ? labels.timedOut : labels.failed;
        openResultModal("err", formatResultHtml(message, "err"));
      }} finally {{
        window.clearTimeout(timeoutId);
        btn.disabled = false;
        btn.textContent = labels.submit;
      }}
    }});
  </script>
</body>
</html>"""
