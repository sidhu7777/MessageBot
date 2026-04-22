# Notification Log Not Updating Fix - Bugfix Design

## Overview

The QR scan overflow booking path (`_book_confirmed_overflow` method in `src/qr/checkin_service.py`) fails to log notification events after creating appointments, while all other booking channels (WhatsApp, Telegram, Web, QR regular slots) correctly log notifications via `save_confirmed_appointment()`. This causes the notification scheduler to miss QR overflow appointments, preventing confirmation SMS from being sent to patients.

The fix adds notification logging to the `_book_confirmed_overflow` method after appointment creation, matching the pattern used by `save_confirmed_appointment()`. The change is minimal and targeted - adding a single try-except wrapped call to `log_notification_event()` with appropriate error handling to prevent booking failures if notification logging fails.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug - when an appointment is created via QR scan overflow booking path
- **Property (P)**: The desired behavior - notification events should be logged for all appointment bookings regardless of channel
- **Preservation**: Existing appointment creation and booking behavior that must remain unchanged by the fix
- **_book_confirmed_overflow**: The method in `src/qr/checkin_service.py` (lines 461-695) that handles QR scan overflow bookings when regular slots are full
- **save_confirmed_appointment**: The method in `src/repositories/booking_repository.py` that handles bookings for other channels and correctly logs notifications
- **log_notification_event**: The method that records notification events in the `appointment_notification_log` table for the scheduler to process
- **overflow booking**: Appointments created outside regular time slots when all scheduled slots are full
- **notification scheduler**: Background process that reads `appointment_notification_log` table and sends SMS/WhatsApp messages

## Bug Details

### Bug Condition

The bug manifests when an appointment is created through the QR scan overflow booking path. The `_book_confirmed_overflow` method successfully creates the appointment and commits it to the database, but does NOT call `log_notification_event()` to record the notification event. This causes the notification scheduler to never send confirmation SMS to the patient.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type BookingRequest
  OUTPUT: boolean
  
  RETURN input.booking_channel == 'qr_scan'
         AND input.uses_overflow_slot == true
         AND appointment_created_successfully(input)
         AND NOT notification_event_logged(input.appointment_id)
END FUNCTION
```

### Examples

- **QR Overflow Booking**: Patient scans QR code when all regular slots are full → appointment created with booking_id > total_regular_slots → NO notification logged → patient never receives confirmation SMS
- **WhatsApp Booking**: Patient books via WhatsApp → `save_confirmed_appointment()` called → notification logged → patient receives confirmation SMS ✓
- **QR Regular Slot Booking**: Patient scans QR code for regular slot → `save_confirmed_appointment()` called → notification logged → patient receives confirmation SMS ✓
- **Edge Case - QR Overflow with Invalid Phone**: Patient scans QR with invalid phone number → appointment created → notification logging should fail gracefully without breaking booking

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Appointment creation logic in `_book_confirmed_overflow` must continue to work exactly as before
- Patient creation/update logic must remain unchanged
- Overflow slot calculation and booking_id assignment must remain unchanged
- Transaction rollback on errors must continue to work
- All other booking channels (WhatsApp, Telegram, Web, QR regular) must continue logging notifications as before

**Scope:**
All inputs that do NOT involve QR scan overflow bookings should be completely unaffected by this fix. This includes:
- WhatsApp bookings via `save_confirmed_appointment()`
- Telegram bookings via `save_confirmed_appointment()`
- Web bookings via `save_confirmed_appointment()`
- QR regular slot bookings via `save_confirmed_appointment()`
- Any other booking paths that already log notifications correctly

## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is clear:

1. **Missing Notification Logging Call**: The `_book_confirmed_overflow` method was implemented without the notification logging step that exists in `save_confirmed_appointment()`
   - `save_confirmed_appointment()` calls `self.log_notification_event()` after successful booking (lines 819-851)
   - `_book_confirmed_overflow` commits the appointment but returns immediately without logging (line 690)

2. **Code Path Divergence**: QR overflow bookings bypass `save_confirmed_appointment()` entirely
   - Regular QR bookings use `save_confirmed_appointment()` → notifications logged ✓
   - Overflow QR bookings use `_book_confirmed_overflow()` → notifications NOT logged ✗

3. **No Error Handling for Notification Failures**: Even if notification logging was added, it needs try-except wrapping to prevent booking failures
   - `save_confirmed_appointment()` wraps notification logging in try-except (lines 847-851)
   - Without this pattern, notification logging errors would rollback the entire booking transaction

## Correctness Properties

Property 1: Bug Condition - QR Overflow Bookings Log Notifications

_For any_ QR scan overflow booking where an appointment is successfully created and committed, the fixed `_book_confirmed_overflow` function SHALL log a notification event with event_type='CONFIRMATION', channel='sms', status='PENDING', and meta_json containing source_channel='qr_scan', enabling the notification scheduler to send confirmation SMS to the patient.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Non-Overflow Booking Behavior

_For any_ booking that does NOT use the QR overflow path (WhatsApp, Telegram, Web, QR regular slots), the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing notification logging functionality through `save_confirmed_appointment()`.

**Validates: Requirements 3.1, 3.2, 3.3**

Property 3: Preservation - Overflow Booking Creation

_For any_ QR overflow booking, the fixed `_book_confirmed_overflow` function SHALL create appointments with exactly the same logic, patient handling, slot calculation, and transaction behavior as the original function, with notification logging being the ONLY addition.

**Validates: Requirements 3.4, 3.5**

## Fix Implementation

### Changes Required

**File**: `src/qr/checkin_service.py`

**Function**: `_book_confirmed_overflow` (lines 461-695)

**Specific Changes**:

1. **Add Notification Logging After Commit**: Insert notification logging code after line 690 (`conn.commit()`)
   - Import `json` module at top of file if not already imported
   - Add try-except block to wrap notification logging
   - Call `self.booking_repository.log_notification_event()` with appropriate parameters

2. **Extract Required Parameters**: Capture parameters needed for notification logging
   - `appointment_id` - already available from line 690 return statement
   - `phone` - already available as method parameter
   - `admin_id` - already available as method parameter
   - `meta_json` - create JSON string with `{"source_channel": "qr_scan"}`

3. **Match save_confirmed_appointment Pattern**: Use identical notification logging pattern
   - event_type='CONFIRMATION'
   - channel='sms'
   - destination=phone
   - status='PENDING'
   - admin_id=admin_id
   - meta_json with source_channel='qr_scan'

4. **Add Error Handling**: Wrap notification logging in try-except
   - Log error if notification logging fails
   - Do NOT rollback transaction or raise exception
   - Allow booking to succeed even if notification logging fails

5. **Add Debug Logging**: Include logging statements for debugging
   - Log when notification logging is attempted
   - Log success/failure of notification logging
   - Match logging pattern from `save_confirmed_appointment()` (lines 819-851)

**Code Location**: Insert after line 690, before the return statement at line 691

**Implementation Pattern** (based on save_confirmed_appointment lines 819-851):
```python
# After conn.commit() at line 690
try:
    import json
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging SMS confirmation notification for QR overflow: appointment_id=%s phone=%s",
        appointment_id,
        phone[:4] + "****" if len(phone) > 4 else phone,
    )
    
    meta = json.dumps({"source_channel": "qr_scan"})
    self.booking_repository.log_notification_event(
        appointment_id=appointment_id,
        event_type="CONFIRMATION",
        channel="sms",
        destination=phone,
        status="PENDING",
        admin_id=admin_id,
        meta_json=meta,
    )
    logger.info("SMS confirmation notification logged successfully: appointment_id=%s", appointment_id)
except Exception as exc:
    logger.error(
        "Failed to log SMS confirmation notification: appointment_id=%s error=%s",
        appointment_id,
        exc,
        exc_info=True,
    )
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code by verifying notification logs are missing, then verify the fix works correctly and preserves existing booking behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that QR overflow bookings do NOT log notifications while other booking paths DO log notifications.

**Test Plan**: Create test appointments through different booking channels and query the `appointment_notification_log` table to verify which bookings logged notifications. Run these tests on the UNFIXED code to observe the missing notification logs for QR overflow bookings.

**Test Cases**:
1. **QR Overflow Booking - No Notification**: Create QR overflow booking → query notification log → EXPECT no log entry (will fail on unfixed code)
2. **WhatsApp Booking - Has Notification**: Create WhatsApp booking → query notification log → EXPECT log entry with source_channel='whatsapp' (passes on unfixed code)
3. **QR Regular Slot - Has Notification**: Create QR regular slot booking → query notification log → EXPECT log entry with source_channel='qr_scan' (passes on unfixed code)
4. **Multiple Overflow Bookings**: Create multiple QR overflow bookings → query notification log → EXPECT no log entries for any (will fail on unfixed code)

**Expected Counterexamples**:
- QR overflow bookings create appointments successfully but `appointment_notification_log` table has no corresponding entries
- Other booking channels (WhatsApp, Telegram, Web, QR regular) have notification log entries
- Root cause confirmed: `_book_confirmed_overflow` does not call `log_notification_event()`

### Fix Checking

**Goal**: Verify that for all QR overflow bookings, the fixed function logs notification events correctly.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  appointment_id := _book_confirmed_overflow_fixed(input)
  notification_log := query_notification_log(appointment_id)
  ASSERT notification_log.exists == true
  ASSERT notification_log.event_type == 'CONFIRMATION'
  ASSERT notification_log.channel == 'sms'
  ASSERT notification_log.status == 'PENDING'
  ASSERT notification_log.meta_json.source_channel == 'qr_scan'
END FOR
```

### Preservation Checking

**Goal**: Verify that for all bookings that do NOT use the QR overflow path, the fixed code produces the same notification logging behavior as the original code.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT notification_logging_behavior_original(input) = notification_logging_behavior_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across different booking channels
- It catches edge cases that manual unit tests might miss (invalid phones, missing parameters, etc.)
- It provides strong guarantees that behavior is unchanged for all non-overflow booking paths

**Test Plan**: Observe notification logging behavior on UNFIXED code for WhatsApp, Telegram, Web, and QR regular bookings, then write property-based tests capturing that behavior and verify it remains unchanged after the fix.

**Test Cases**:
1. **WhatsApp Booking Preservation**: Create WhatsApp booking on unfixed code → observe notification log entry → apply fix → verify identical notification log entry
2. **Telegram Booking Preservation**: Create Telegram booking on unfixed code → observe notification log entry → apply fix → verify identical notification log entry
3. **QR Regular Slot Preservation**: Create QR regular booking on unfixed code → observe notification log entry → apply fix → verify identical notification log entry
4. **Overflow Booking Creation Preservation**: Create QR overflow booking on unfixed code → observe appointment details → apply fix → verify identical appointment details (only notification log should differ)

### Unit Tests

- Test `_book_confirmed_overflow` creates appointment and logs notification with correct parameters
- Test notification logging failure does not prevent appointment creation (error handling)
- Test notification log entry has correct event_type, channel, status, and meta_json
- Test edge cases: invalid phone number, missing admin_id, database connection failures

### Property-Based Tests

- Generate random QR overflow booking requests and verify all log notifications correctly
- Generate random booking requests across all channels and verify preservation of existing notification logging
- Test that appointment creation logic remains unchanged across many scenarios
- Test error handling: generate random exceptions during notification logging and verify bookings still succeed

### Integration Tests

- Test full QR overflow booking flow: scan QR → create overflow appointment → verify notification logged → verify scheduler picks up notification
- Test notification scheduler processes QR overflow notifications correctly
- Test SMS sending for QR overflow bookings works end-to-end
- Test mixed booking scenario: create bookings via multiple channels and verify all log notifications correctly
