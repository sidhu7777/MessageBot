import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parent.parent
QR_SVG_PATH = REPO_ROOT / "google-drive-qr.svg"
EXPECTED_TARGET_URL = (
    "https://drive.google.com/file/d/1rudNf86g4mQvbC35J1dLClLGhQCnZHnh/view?usp=drivesdk"
)
EXPECTED_QR_IMAGE_URL = (
    "https://api.qrserver.com/v1/create-qr-code/"
    "?size=300x300&format=svg&data="
    "https%3A%2F%2Fdrive.google.com%2Ffile%2Fd%2F1rudNf86g4mQvbC35J1dLClLGhQCnZHnh%2Fview%3Fusp%3Ddrivesdk"
)


class TestGoogleDriveQrAsset(unittest.TestCase):
    def test_qr_svg_exists(self) -> None:
        self.assertTrue(QR_SVG_PATH.exists(), f"Missing QR asset: {QR_SVG_PATH}")

    def test_qr_svg_points_to_expected_google_drive_url(self) -> None:
        tree = ET.parse(QR_SVG_PATH)
        root = tree.getroot()
        image = root.find("{http://www.w3.org/2000/svg}image")
        self.assertIsNotNone(image, "Expected an <image> node in the SVG")

        href = image.attrib.get("href") or image.attrib.get("{http://www.w3.org/1999/xlink}href")
        self.assertEqual(href, EXPECTED_QR_IMAGE_URL)

        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        encoded_target = params.get("data")
        self.assertEqual(encoded_target, [EXPECTED_TARGET_URL])

    def test_qr_svg_contains_manual_scan_copy(self) -> None:
        contents = QR_SVG_PATH.read_text(encoding="utf-8")
        self.assertIn("Scan to open the Google Drive file", contents)
        self.assertIn("1rudNf86g4mQvbC35J1dLClLGhQCnZHnh", contents)


if __name__ == "__main__":
    unittest.main()
