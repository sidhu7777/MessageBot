import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.qr_routes import register_qr_routes


class StubQrCheckinService:
    def resolve_doctor_and_clinic(self, doctor_id: int, clinic_id: int) -> tuple[str, str]:
        return f"Doctor {doctor_id}", f"Clinic {clinic_id}"


def _build_client() -> TestClient:
    app = FastAPI()
    register_qr_routes(
        app,
        qr_checkin_service=StubQrCheckinService(),
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        log_event_fn=lambda *args, **kwargs: None,
    )
    return TestClient(app)


class TestQrGenerate(unittest.TestCase):
    def test_generate_qr_returns_preview_and_download_path(self) -> None:
        client = _build_client()

        response = client.post(
            "/qr/generate",
            json={"doctor_id": 1, "clinic_id": 5},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["doctor_id"], 1)
        self.assertEqual(payload["clinic_id"], 5)
        self.assertEqual(payload["doctor_name"], "Doctor 1")
        self.assertEqual(payload["clinic_name"], "Clinic 5")
        self.assertEqual(payload["mime_type"], "image/svg+xml")
        self.assertEqual(payload["filename"], "qr-doctor-1-clinic-5-1-5.svg")
        self.assertEqual(payload["download_path"], "/qr/generate/download?doctor_id=1&clinic_id=5")
        self.assertTrue(payload["preview_data_url"].startswith("data:image/svg+xml;base64,"))

    def test_generate_qr_download_returns_svg_attachment(self) -> None:
        client = _build_client()

        response = client.get("/qr/generate/download?doctor_id=1&clinic_id=5")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("image/svg+xml"))
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="qr-doctor-1-clinic-5-1-5.svg"',
        )
        self.assertIn("<svg", response.text)


if __name__ == "__main__":
    unittest.main()
