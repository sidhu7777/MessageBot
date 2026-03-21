import html as html_escape


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
    doctor_name_safe = html_escape.escape(doctor_name or "Doctor")
    clinic_name_safe = html_escape.escape(clinic_name or "Clinic")
    patient_name_safe = html_escape.escape(patient_name or "")
    phone_number_safe = html_escape.escape(phone_number or "")
    result_message_safe = html_escape.escape(result_message or "")
    lang = language if language in {"en", "hi", "hinglish"} else "en"
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
  </style>
</head>
<body>
  <section class=\"card\">
    <div class=\"hero\">
      <span class=\"kicker\" id=\"kicker\">Book Your Appointment</span>
      <h1 id=\"title\">Welcome to Dr. {doctor_name_safe} clinic</h1>
      <p class=\"subtitle\" id=\"subtitle\">Clinic: {clinic_name_safe}</p>
    </div>
    <form class=\"form-wrap\" id=\"checkinForm\" method=\"post\" action=\"/qr/checkin/submit?doctor_id={doctor_id}&clinic_id={clinic_id}\">
      <div class=\"grid\">
        <label>
          <span id=\"nameLabel\">Full Name</span>
          <input id=\"patientName\" name=\"patient_name\" maxlength=\"120\" value=\"{patient_name_safe}\" required />
        </label>
        <label>
          <span id=\"phoneLabel\">Phone Number</span>
          <input id=\"phoneNumber\" name=\"phone_number\" maxlength=\"20\" value=\"{phone_number_safe}\" required />
        </label>
      </div>
      <button id=\"submitBtn\" type=\"submit\">Submit</button>
      <div id=\"result\" class=\"result{result_class}\">{result_message_safe}</div>
      <input type=\"hidden\" id=\"doctorId\" value=\"{doctor_id}\" />
      <input type=\"hidden\" id=\"clinicId\" value=\"{clinic_id}\" />
    </form>
  </section>

  <script>
    const t = {{
      en: {{
        kicker: "Book Your Appointment",
        title: "Welcome to Dr. {doctor_name_safe} clinic",
        subtitle: "Clinic: {clinic_name_safe}",
        nameLabel: "Full Name",
        phoneLabel: "Phone Number",
        submit: "Submit",
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

    function normalizeDetectedLanguage(raw) {{
      const value = String(raw || "").toLowerCase();
      if (value.startsWith("hi")) return "hi";
      if (value.startsWith("en")) return "en";
      return "en";
    }}

    function detectBrowserLanguage() {{
      const list = Array.isArray(navigator.languages) ? navigator.languages : [];
      const first = list.length ? list[0] : navigator.language;
      return normalizeDetectedLanguage(first);
    }}

    function applyLanguage(lang) {{
      const d = t[lang] || t.en;
      activeLanguage = lang in t ? lang : "en";
      document.getElementById("kicker").textContent = d.kicker;
      document.getElementById("title").textContent = d.title;
      document.getElementById("subtitle").textContent = d.subtitle;
      document.getElementById("nameLabel").textContent = d.nameLabel;
      document.getElementById("phoneLabel").textContent = d.phoneLabel;
      document.getElementById("submitBtn").textContent = d.submit;
    }}

    applyLanguage(lockLanguage ? serverLang : detectBrowserLanguage());

    const checkinForm = document.getElementById("checkinForm");
    checkinForm.addEventListener("submit", async (e) => {{
      e.preventDefault();
      e.stopPropagation();
      const forcedLangParam = lockLanguage ? `?lang=${{encodeURIComponent(serverLang)}}` : "";
      const submitUrl = `/qr/checkin/submit${{forcedLangParam}}`;
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
          result.classList.add("err");
          result.textContent = currentTexts.serverError;
          btn.disabled = false;
          return;
        }}
        const renderResultMessage = (message) => {{
          const safe = String(message || currentTexts.done)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          const withBold = safe.replace(
            /(Token ID:\s*[A-Za-z0-9\-]+\.?)/gi,
            "<strong>$1</strong>"
          );
          result.innerHTML = withBold.replace(/\n/g, "<br>");
        }};
        if (!resp.ok) {{
          result.classList.add("err");
          result.textContent = data.detail || data.message || currentTexts.requestFailed;
        }} else {{
          const status = data.status || "";
          if (status === "booked") result.classList.add("ok");
          else if (status === "overflow" || status === "active_booking") result.classList.add("warn");
          else result.classList.add("err");
          renderResultMessage(data.message || currentTexts.done);
        }}
      }} catch (_err) {{
        result.classList.add("err");
        result.textContent = currentTexts.submitFailed;
      }} finally {{
        btn.disabled = false;
      }}
    }}, true);
  </script>
</body>
</html>"""
