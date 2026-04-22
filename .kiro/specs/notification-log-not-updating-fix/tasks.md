# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - QR Overflow Bookings Missing Notification Logs
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: For deterministic bugs, scope the property to the concrete failing case(s) to ensure reproducibility
  - Test that QR overflow bookings create appointments but do NOT log notification events (from Bug Condition in design)
  - The test assertions should match the Expected Behavior Properties from design: notification events SHOULD be logged with event_type='CONFIRMATION', channel='sms', status='PENDING', meta_json containing source_channel='qr_scan'
  - Create test QR overflow booking by calling `_book_confirmed_overflow` with valid parameters
  - Query `appointment_notification_log` table for the created appointment_id
  - Assert that NO notification log entry exists (this will FAIL on unfixed code, confirming the bug)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: Bug Condition specification from design_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Overflow Booking Notification Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-buggy inputs (WhatsApp, Telegram, Web, QR regular slot bookings)
  - Write property-based tests capturing observed behavior patterns from Preservation Requirements
  - Property-based testing generates many test cases for stronger guarantees
  - Test that WhatsApp bookings via `save_confirmed_appointment()` log notifications with source_channel='whatsapp'
  - Test that Telegram bookings via `save_confirmed_appointment()` log notifications with source_channel='telegram'
  - Test that QR regular slot bookings via `save_confirmed_appointment()` log notifications with source_channel='qr_scan'
  - Test that appointment creation logic in `_book_confirmed_overflow` produces identical appointments (patient handling, slot calculation, booking_id assignment)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: Preservation Requirements from design (3.1, 3.2, 3.3, 3.4, 3.5)_

- [-] 3. Fix for QR overflow booking notification logging

  - [x] 3.1 Implement the fix in `_book_confirmed_overflow` method
    - Open `src/qr/checkin_service.py` and locate the `_book_confirmed_overflow` method (lines 461-695)
    - Add notification logging code after line 690 (`conn.commit()`) and before the return statement at line 691
    - Import `json` module at top of file if not already imported
    - Add try-except block wrapping the notification logging call
    - Extract required parameters: appointment_id (from return), phone (method param), admin_id (method param), doctor_id (method param)
    - Create meta_json with `json.dumps({"source_channel": "qr_scan"})`
    - Call `self.booking_repository.log_notification_event()` with parameters: appointment_id, event_type='CONFIRMATION', channel='sms', destination=phone, status='PENDING', admin_id=admin_id, doctor_id=doctor_id, meta_json=meta
    - Add debug logging before and after the call (match pattern from save_confirmed_appointment lines 819-851)
    - Add error handling in except block: log error but do NOT rollback transaction or raise exception
    - Verify the code matches the implementation pattern from design document
    - _Bug_Condition: isBugCondition(input) where input.booking_channel == 'qr_scan' AND input.uses_overflow_slot == true_
    - _Expected_Behavior: notification event logged with event_type='CONFIRMATION', channel='sms', status='PENDING', meta_json containing source_channel='qr_scan'_
    - _Preservation: Appointment creation logic, patient handling, slot calculation, transaction behavior remain unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - QR Overflow Bookings Log Notifications
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - Verify notification log entry exists with correct event_type, channel, status, and meta_json
    - _Requirements: Expected Behavior Properties from design (2.1, 2.2, 2.3)_

  - [ ] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Overflow Booking Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)
    - Verify WhatsApp, Telegram, Web, and QR regular slot bookings still log notifications correctly
    - Verify appointment creation logic in `_book_confirmed_overflow` remains unchanged
    - _Requirements: Preservation Requirements from design (3.1, 3.2, 3.3, 3.4, 3.5)_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run all unit tests for `_book_confirmed_overflow` and `save_confirmed_appointment`
  - Run all property-based tests for bug condition and preservation
  - Run integration tests for full QR overflow booking flow
  - Verify notification scheduler picks up QR overflow notifications
  - Verify SMS sending works end-to-end for QR overflow bookings
  - Ensure all tests pass, ask the user if questions arise
