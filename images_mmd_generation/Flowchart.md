# Current Appointment FSM Flow

This is the current FSM flow based on the code in:
- [appointment_fsm.py](c:\Users\91832\Desktop\Message_bot\src\fsm\appointment_fsm.py)
- [init_availability.py](c:\Users\91832\Desktop\Message_bot\src\fsm\handlers\init_availability.py)
- [booking.py](c:\Users\91832\Desktop\Message_bot\src\fsm\handlers\booking.py)
- [existing.py](c:\Users\91832\Desktop\Message_bot\src\fsm\handlers\existing.py)

```mermaid
flowchart TD
    Start([Start / New inbound turn]) --> Init[INIT]

    Init -->|hello / hi / start| InitGreeting[Greeting + choose 1 or 2]
    Init -->|1 from menu| AskBookingFor[ASK_BOOKING_FOR]
    Init -->|2 from menu| AskAvailDate[ASK_AVAILABILITY_DATE]
    Init -->|direct booking intent| AskBookingFor
    Init -->|direct availability intent| AskAvailDate
    Init -->|existing active booking found| ExistingAction[ASK_EXISTING_BOOKING_ACTION]
    Init -->|3 unclear attempts| FinalCheck[Final booking check]

    AskAvailDate -->|pick date| AskAvailDetails[ASK_AVAILABILITY_DETAILS]
    AskAvailDate -->|0| Init

    AskAvailDetails -->|show availability reply| AskAvailDetails
    AskAvailDetails -->|booking intent / restart / 1| AskBookingFor
    AskAvailDetails -->|0| AskAvailDate

    AskBookingFor -->|1 Self| SelfBranch{Known patient?}
    AskBookingFor -->|2 Another person| AskName[ASK_NAME]
    AskBookingFor -->|0| Init

    SelfBranch -->|known patient + known phone| AskClinic[ASK_CLINIC]
    SelfBranch -->|known patient, phone missing| AskPhone[ASK_PHONE]
    SelfBranch -->|unknown patient| AskName

    AskName -->|valid name| AskPhone
    AskName -->|availability / other reroute| Init
    AskName -->|invalid| AskName
    AskName -->|0| AskBookingFor

    AskPhone -->|valid/same number| AskClinic
    AskPhone -->|invalid| AskPhone
    AskPhone -->|0| AskName

    AskClinic -->|pick clinic| AskDate[ASK_DATE]
    AskClinic -->|invalid clinic| AskClinic
    AskClinic -->|0 normal path| AskPhone
    AskClinic -->|0 known self path| AskBookingFor

    AskDate -->|pick date| AskTime[ASK_TIME]
    AskDate -->|invalid| AskDate
    AskDate -->|0| AskClinic

    AskTime -->|pick valid slot| TimeRoute{Reschedule flow?}
    AskTime -->|invalid / unavailable| AskTime
    AskTime -->|0| AskDate

    TimeRoute -->|no| Confirm[CONFIRM]
    TimeRoute -->|yes| ConfirmReschedule[CONFIRM_RESCHEDULE]

    Confirm -->|1 / yes| Completed[COMPLETED]
    Confirm -->|2 / change| ChangeField[ASK_CHANGE_FIELD]
    Confirm -->|0| AskTime

    ChangeField -->|change clinic| AskClinic
    ChangeField -->|change time| AskTime
    ChangeField -->|change name| AskName
    ChangeField -->|change phone| AskPhone
    ChangeField -->|0| Confirm

    ExistingAction -->|1 Keep| Completed
    ExistingAction -->|2 Cancel| Completed
    ExistingAction -->|3 Reschedule| ExistingPick{Multiple active bookings?}
    ExistingAction -->|4 Book for another person| MaxActive[ASK_MAX_ACTIVE_BOOKINGS_ACTION]
    ExistingAction -->|0| Init

    ExistingPick -->|yes| PickBooking[ASK_EXISTING_BOOKING_PICK]
    ExistingPick -->|no| AskClinic

    PickBooking -->|select booking for cancel| Completed
    PickBooking -->|select booking for reschedule| AskClinic
    PickBooking -->|0| ExistingAction

    MaxActive -->|1 Cancel existing| PickOrCancel{Multiple active bookings?}
    MaxActive -->|2 Reschedule existing| PickOrReschedule{Multiple active bookings?}
    MaxActive -->|0| ExistingAction

    PickOrCancel -->|one booking| Completed
    PickOrCancel -->|multiple| PickBooking

    PickOrReschedule -->|one booking| AskClinic
    PickOrReschedule -->|multiple| PickBooking

    ConfirmReschedule -->|1 Confirm| Completed
    ConfirmReschedule -->|2 Change details| AskTime
    ConfirmReschedule -->|0| ExistingAction

    Completed -->|active booking still exists| ExistingAction
    Completed -->|new booking intent| AskBookingFor
    Completed -->|hello / other| Init

    Cancelled[CANCELLED] -->|booking intent / restart| AskName
    Cancelled -->|hello / other| Init
```
