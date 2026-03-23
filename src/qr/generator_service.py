from __future__ import annotations

import base64
import io
import re
from urllib.parse import urlencode

import qrcode
from qrcode.image.svg import SvgPathImage


def build_qr_booking_url(*, base_url: str, doctor_id: int, clinic_id: int) -> str:
    root = (base_url or "").strip().rstrip("/")
    query = urlencode({"doctor_id": int(doctor_id), "clinic_id": int(clinic_id)})
    return f"{root}/qr/checkin?{query}"


def build_qr_svg(*, url: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def svg_to_data_url(svg_markup: str) -> str:
    encoded = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def build_download_filename(*, doctor_name: str, clinic_name: str, doctor_id: int, clinic_id: int) -> str:
    raw = f"{doctor_name or 'doctor'}-{clinic_name or 'clinic'}-{doctor_id}-{clinic_id}".strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-") or f"doctor-{doctor_id}-clinic-{clinic_id}"
    return f"qr-{slug}.svg"
