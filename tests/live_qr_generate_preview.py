import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

import httpx


def build_report_html(*, payload: dict, base_url: str, absolute_download_url: str) -> str:
    preview_data_url = str(payload["preview_data_url"])
    filename = str(payload["filename"])
    doctor_name = str(payload["doctor_name"])
    clinic_name = str(payload["clinic_name"])
    doctor_id = int(payload["doctor_id"])
    clinic_id = int(payload["clinic_id"])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>QR Generate Live Preview</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 0;
      padding: 24px;
      background: #f5f7fb;
      color: #1c2430;
    }}
    .card {{
      max-width: 760px;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #d9e1ec;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(20, 34, 56, 0.08);
    }}
    h1 {{
      margin-top: 0;
      font-size: 28px;
    }}
    .meta {{
      margin: 12px 0 20px 0;
      line-height: 1.6;
    }}
    .preview {{
      display: grid;
      place-items: center;
      background: #f9fbff;
      border: 1px dashed #b8c7dc;
      border-radius: 12px;
      padding: 20px;
      margin: 20px 0;
    }}
    img {{
      max-width: 320px;
      width: 100%;
      height: auto;
      background: #fff;
    }}
    a.button {{
      display: inline-block;
      padding: 12px 16px;
      border-radius: 10px;
      background: #0f766e;
      color: #fff;
      text-decoration: none;
      font-weight: 700;
    }}
    code {{
      background: #eef3fa;
      padding: 2px 6px;
      border-radius: 6px;
    }}
    pre {{
      background: #101826;
      color: #dbe7ff;
      padding: 16px;
      border-radius: 12px;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <main class="card">
    <h1>QR Generate Live Preview</h1>
    <div class="meta">
      <div><strong>Backend:</strong> <code>{base_url}</code></div>
      <div><strong>Doctor:</strong> {doctor_name} ({doctor_id})</div>
      <div><strong>Clinic:</strong> {clinic_name} ({clinic_id})</div>
      <div><strong>Filename:</strong> <code>{filename}</code></div>
    </div>
    <section class="preview">
      <img src="{preview_data_url}" alt="Generated QR preview" />
    </section>
    <p>
      <a class="button" href="{absolute_download_url}" download="{filename}">Download QR SVG</a>
    </p>
    <h2>Raw API Response</h2>
    <pre>{json.dumps(payload, indent=2)}</pre>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a live QR preview report from the backend API.")
    parser.add_argument("--base-url", required=True, help="Backend base URL, for example http://127.0.0.1:8000")
    parser.add_argument("--doctor-id", type=int, required=True)
    parser.add_argument("--clinic-id", type=int, required=True)
    parser.add_argument(
        "--output-dir",
        default="tests/artifacts",
        help="Directory where the HTML report and downloaded SVG should be written",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{base_url}/qr/generate",
            json={"doctor_id": args.doctor_id, "clinic_id": args.clinic_id},
        )
        response.raise_for_status()
        payload = response.json()

        required_keys = {
            "doctor_id",
            "clinic_id",
            "doctor_name",
            "clinic_name",
            "mime_type",
            "filename",
            "preview_data_url",
            "download_path",
        }
        missing = sorted(required_keys.difference(payload))
        if missing:
            raise RuntimeError(f"QR generate response is missing keys: {', '.join(missing)}")

        if str(payload["mime_type"]) != "image/svg+xml":
            raise RuntimeError(f"Expected image/svg+xml but got {payload['mime_type']!r}")
        if not str(payload["preview_data_url"]).startswith("data:image/svg+xml;base64,"):
            raise RuntimeError("preview_data_url is not an SVG data URL")

        absolute_download_url = urljoin(f"{base_url}/", str(payload["download_path"]).lstrip("/"))
        download_response = client.get(absolute_download_url)
        download_response.raise_for_status()
        if "image/svg+xml" not in download_response.headers.get("content-type", ""):
            raise RuntimeError("Download endpoint did not return an SVG content-type")

    svg_path = output_dir / str(payload["filename"])
    svg_path.write_bytes(download_response.content)

    report_path = output_dir / "qr_generate_preview.html"
    report_html = build_report_html(
        payload=payload,
        base_url=base_url,
        absolute_download_url=absolute_download_url,
    )
    report_path.write_text(report_html, encoding="utf-8")

    print(f"HTML preview report: {report_path}")
    print(f"Downloaded SVG file: {svg_path}")
    print(f"Download URL: {absolute_download_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
