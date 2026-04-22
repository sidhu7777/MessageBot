"""
Bug Condition Exploration Test for QR Overflow Notification Logging

This test MUST FAIL on unfixed code to confirm the bug exists.
When it passes after the fix, it validates the expected behavior.
"""

import json
from datetime import date, time
from types import SimpleNamespace


class FakeConnection:
    def __init__(self):
        self.cursors = []
        self.commits = 0
        self.rollbacks = 0
        self.in_transaction = False
        self.appointments = {}
        self.notification_logs = {}
        self.next_appointment_id = 100

    def cursor(self, dictionary=False):
        cur = FakeCursor(self, dictionary=dictionary)
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commits += 1
        self.in_transaction = False

    def rollback(self):
        self.rollbacks += 1
        self.in_transaction = False

    def start_transaction(self):
        self.in_transaction = True

    def close(self):
        pass


class FakeCursor:
    def __init__(self, conn, dictionary=False):
        self.conn = conn
        self.dictionary = dictionary
        self.executed = []
        self._results = []
        self._result_index = 0
        self.lastrowid = 0

    def execute(self, query, params=None):
        self.executed.append((query, params))
        query_lower = query.lower().strip()

        # Handle INSERT INTO appointment
        if "insert into appointment" in query_lower:
            self.conn.next_appointment_id += 1
            self.lastrowid = self.conn.next_appointment_id
            # Store appointment details
            self.conn.appointments[self.lastrowid] = {
                "appointment_id": self.lastrowid,
                "patient_id": params[0] if params else None,
                "doctor_id": params[1] if params else None,
                "clinic_id": params[2] if params else None,
                "admin_id": params[3] if params else None,
                "status": "BOOKED",
            }
            return

        # Handle INSERT INTO appointment_notification_log
        if "insert into appointment_notification_log" in query_lower:
            appointment_id = params[0] if params else None
            notification_id = len(self.conn.notification_logs) + 1
            self.conn.notification_logs[notification_id] = {
                "notification_id": notification_id,
                "appointment_id": appointment_id,
                "event_type": params[1] if len(params) > 1 else None,
                "channel": params[2] if len(params) > 2 else None,
                "destination": params[3] if len(params) > 3 else None,
                "status": params[4] if len(params) > 4 else None,
                "meta_json": params[6] if len(params) > 6 else None,
            }
            self.lastrowid = notification_id
            return

        # Handle SELECT from appointment_notification_log
        if "select" in query_lower and "appointment_notification_log" in query_lower:
            # Return notification logs for the queried appointment_id
            if params:
                appointment_id = params[0]
                matching_logs = [
                    log for log in self.conn.notification_logs.values()
                    if log["appointment_id"] == appointment_id
                ]
                self._results = matching_logs
            else:
                self._results = list(self.conn.notification_logs.values())
            self._result_index = 0
            return

        # Default responses for other queries
        if "select" in query_lower and "doctor_clinic_schedule" in query_lower:
            # Return mock schedule
            self._results = [
                {
                    "start_time": time(9, 0),
                    "end_time": time(17, 0),
                    "slot_duration": 15,
                }
            ]
            self._result_index = 0
        elif "select patient_id from patients" in query_lower:
            # No existing patient
            self._results = []
            self._result_index = 0
        elif "insert into patients" in query_lower:
            self.lastrowid = 50
        elif "select a.start_time from" in query_lower and "appointment" in query_lower:
            # No existing overflow appointments
            self._results = []
            self._result_index = 0
        elif "select appointment_id, status from" in query_lower:
            # No conflicting appointments
            self._results = []
            self._result_index = 0

    def fetchone(self):
        if self._result_index < len(self._results):
            result = self._results[self._result_index]
            self._result_index += 1
            return result
        return None

    def fetchall(self):
        results = self._results[self._result_index:]
        self._result_index = len(self._results)
        return results

    def close(self):
        pass


class FakeBookingRepository:
    def __init__(self, conn):
        self.conn = conn
        self.notification_events_logged = []

    def _connect(self):
        return self.conn

    def _appointment_table(self):
        return "appointment"

    def _use_appointment_mode(self):
        return True

    def _table_columns(self, table_name):
        if table_name == "patients":
            return {"patient_id", "full_name", "admin_id", "doctor_id", "phone", "patient_type", "booking_id"}
        elif table_name == "appointment":
            return {"appointment_id", "patient_id", "doctor_id", "clinic_id", "admin_id", "status",
                    "appointment_date", "start_time", "end_time", "booking_id", "channel"}
        return set()

    def _normalized_phone_sql_expr(self, column_expr):
        return f"REPLACE(REPLACE(REPLACE(REPLACE(LOWER(COALESCE({column_expr}, '')), 'whatsapp:', ''), '+', ''), '-', ''), ' ', '')"

    def _parse_time_value(self, raw):
        if isinstance(raw, time):
            return raw
        return None

    @staticmethod
    def _normalize_schedules(schedules):
        return [(s["start_time"], s["end_time"], s["slot_duration"]) for s in schedules]

    def log_notification_event(self, **kwargs):
        """Track notification events for testing"""
        self.notification_events_logged.append(kwargs)
        # Simulate inserting into fake database (outside transaction)
        notification_id = len(self.conn.notification_logs) + 1
        self.conn.notification_logs[notification_id] = {
            "notification_id": notification_id,
            "appointment_id": kwargs.get("appointment_id"),
            "event_type": kwargs.get("event_type"),
            "channel": kwargs.get("channel"),
            "destination": kwargs.get("destination"),
            "status": kwargs.get("status"),
            "meta_json": kwargs.get("meta_json", ""),
        }


def test_qr_overflow_booking_missing_notification_log():
    """
    Bug Condition Exploration Test - Property 1
    
    This test MUST FAIL on unfixed code to confirm the bug exists.
    
    Test that QR overflow bookings create appointments but do NOT log
    notification events in the appointment_notification_log table.
    
    EXPECTED OUTCOME ON UNFIXED CODE: FAIL (proves bug exists)
    EXPECTED OUTCOME ON FIXED CODE: PASS (proves bug is fixed)
    """
    from src.qr.checkin_service import QrCheckinService

    # Setup fake database connection
    fake_conn = FakeConnection()
    fake_booking_repo = FakeBookingRepository(fake_conn)

    # Create QR checkin service
    qr_service = QrCheckinService(
        booking_repository=fake_booking_repo,
        scheduling_repository=None,
    )

    # Call _book_confirmed_overflow (the buggy method)
    appointment_id, booking_number, slot_date, slot_time = qr_service._book_confirmed_overflow(
        admin_id=1,
        doctor_id=4,
        clinic_id=11,
        patient_name="Test Patient",
        phone="6394753866",
        target_session=(time(9, 0), time(17, 0), 15),
    )

    # Verify appointment was created
    assert appointment_id is not None, "Appointment should be created"
    assert appointment_id in fake_conn.appointments, "Appointment should exist in database"

    # Query notification log for this appointment
    cur = fake_conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM appointment_notification_log WHERE appointment_id = %s",
        (appointment_id,)
    )
    notification_logs = cur.fetchall()

    # CRITICAL ASSERTION: This will FAIL on unfixed code
    assert len(notification_logs) > 0, (
        f"BUG CONFIRMED: QR overflow booking (appointment_id={appointment_id}) "
        f"did NOT log notification event. Expected 1 notification log entry, found 0."
    )

    # Verify notification log has correct properties (Expected Behavior)
    notification_log = notification_logs[0]
    assert notification_log["event_type"] == "CONFIRMATION", "Event type should be CONFIRMATION"
    assert notification_log["channel"] == "sms", "Channel should be sms"
    assert notification_log["status"] == "PENDING", "Status should be PENDING"
    assert notification_log["destination"] == "6394753866", "Destination should be patient phone"

    # Verify meta_json contains source_channel
    meta_json = json.loads(notification_log["meta_json"])
    assert meta_json.get("source_channel") == "qr_scan", "Meta JSON should contain source_channel='qr_scan'"

    print(f"✓ Bug condition test PASSED - notification logged for appointment {appointment_id}")


if __name__ == "__main__":
    test_qr_overflow_booking_missing_notification_log()
    print("All bug condition exploration tests passed!")
