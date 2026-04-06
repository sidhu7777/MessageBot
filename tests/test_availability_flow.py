import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient


class _Clinic:
    def __init__(self, clinic_id: int, clinic_name: str, location: str, today_slots: int) -> None:
        self.clinic_id = clinic_id
        self.clinic_name = clinic_name
        self.location = location
        self.today_slots = today_slots


class _FakeSchedulingRepo:
    def default_doctor_id(self, admin_id=None):
        return 1

    def list_clinics_for_doctor(self, doctor_id: int, admin_id=None, limit: int = 10):
        return [
            _Clinic(1, "City Care Clinic", "MG Road, Hyderabad", 3),
            _Clinic(2, "Sunrise Health Center", "KPHB, Hyderabad", 2),
        ][:limit]

    def list_available_times(self, doctor_id: int, clinic_id: int, slot_date: str, admin_id=None, limit: int = 50):
        today = date.today().isoformat()
        if slot_date != today:
            return []
        if clinic_id == 1:
            return ["09:00", "09:30", "10:00"]
        if clinic_id == 2:
            return ["18:00", "18:30"]
        return []

    def list_available_dates(self, doctor_id: int, clinic_id: int, admin_id=None, limit: int = 1):
        return [date.today().isoformat()][:limit]


class AvailabilityFlowTests(unittest.TestCase):
    def _new_fsm(self, scheduling_repo=None) -> AppointmentFSM:
        return AppointmentFSM(
            llm_client=LLMClient(model="qwen3:0.6b", provider="mock", timeout_seconds=30),
            enable_llm_polish=False,
            mixed_response_language="auto",
            scheduling_repository=scheduling_repo,
        )

    def test_availability_typo_routes_and_uses_date(self) -> None:
        fsm = self._new_fsm()
        today = date.today().isoformat()
        reply = fsm.handle("i need to know doctor availabilyty today")
        self.assertEqual(fsm.state, "ASK_AVAILABILITY_DATE")
        self.assertIn(today, reply)
        reply = fsm.handle("1")
        self.assertEqual(fsm.state, "ASK_AVAILABILITY_DETAILS")
        self.assertIn(today, reply)
        self.assertIn("doctor availability", reply.lower())

    def test_availability_shows_slot_summary_for_today(self) -> None:
        fsm = self._new_fsm(scheduling_repo=_FakeSchedulingRepo())
        today = date.today().isoformat()
        reply = fsm.handle("i need to know doctor availabilyty today")
        self.assertEqual(fsm.state, "ASK_AVAILABILITY_DATE")
        self.assertIn(today, reply)
        reply = fsm.handle("1")
        self.assertEqual(fsm.state, "ASK_AVAILABILITY_DETAILS")
        self.assertIn(today, reply)
        self.assertTrue(
            ("doctor availability" in reply.lower()) or ("share doctor name" in reply.lower()),
            reply,
        )


if __name__ == "__main__":
    unittest.main()
