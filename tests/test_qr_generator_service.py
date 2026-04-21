from __future__ import annotations

import struct
import zlib
from pathlib import Path

import qrcode


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _write_grayscale_png(*, path: Path, pixels: list[list[int]]) -> None:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), level=9)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)


def _qr_matrix_to_pixels(*, matrix: list[list[bool]], scale: int, border: int) -> list[list[int]]:
    size = len(matrix)
    image_size = (size + (border * 2)) * scale
    pixels = [[255 for _ in range(image_size)] for _ in range(image_size)]

    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            color = 0 if cell else 255
            start_y = (y + border) * scale
            start_x = (x + border) * scale
            for py in range(start_y, start_y + scale):
                for px in range(start_x, start_x + scale):
                    pixels[py][px] = color

    return pixels


def test_generate_doctor_qr_png_into_images_folder() -> None:
    url = "https://daptoservices.vinfocom.co.in/whatsapp/web/Dr.SanjayVinayak"
    project_root = Path(__file__).resolve().parent.parent
    images_dir = project_root / "images"
    output_path = images_dir / "sanjayvinayak_qr_image.png"

    images_dir.mkdir(parents=True, exist_ok=True)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    pixels = _qr_matrix_to_pixels(matrix=matrix, scale=16, border=1)
    _write_grayscale_png(path=output_path, pixels=pixels)

    png_bytes = output_path.read_bytes()
    assert output_path.exists()
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
