# Current System Architecture And Data Flow

This diagram reflects the current runtime wiring in:
- [main.py](c:\Users\91832\Desktop\Message_bot\main.py)
- [webhooks.py](c:\Users\91832\Desktop\Message_bot\src\api\webhooks.py)
- [session_store.py](c:\Users\91832\Desktop\Message_bot\src\session_store.py)
- [kafka_turn_bridge.py](c:\Users\91832\Desktop\Message_bot\src\runtime\kafka_turn_bridge.py)
- [kafka_notification_bridge.py](c:\Users\91832\Desktop\Message_bot\src\runtime\kafka_notification_bridge.py)
- [booking_repository.py](c:\Users\91832\Desktop\Message_bot\src\repositories\booking_repository.py)
- [scheduling_repository.py](c:\Users\91832\Desktop\Message_bot\src\repositories\scheduling_repository.py)
- [checkin_service.py](c:\Users\91832\Desktop\Message_bot\src\qr\checkin_service.py)

```mermaid
flowchart LR
    subgraph Channels[Inbound Channels]
        TG[Telegram user]
        WA1[WhatsApp via Twilio]
        WA2[WhatsApp via Meta]
        WA3[WhatsApp via Infobip]
        QR[QR / Web patient page]
    end

    subgraph FastAPI[FastAPI App]
        Main[main.py]
        WH[register_webhook_routes]
        QRPage[GET /qr/checkin]
        QRSubmit[POST /qr/checkin/submit]
        Health[/health + /health/queue]
    end

    subgraph Runtime[Runtime Layer]
        Dedup[PersistentMessageSidStore]
        Guard[UserProcessingGuard]
        Buffer[UserTurnBuffer]
        KTurn[KafkaTurnBridge]
        TurnQ[TurnQueueProcessor]
        Sess[SessionManager]
        FSM[AppointmentFSM]
        Delivery[ChannelDelivery]
        Logger[chat_logger]
    end

    subgraph Async[Async / Background]
        KNotif[KafkaNotificationBridge]
        Scheduler[AutomationScheduler]
        OverflowPoll[Overflow poller]
        CachePoll[Doctor cache invalidation poller]
    end

    subgraph Service[Domain Services]
        QRService[QrCheckinService]
        LLM[LLMClient + llm.tasks]
        NLU[extractors + initial_router + language_detector]
    end

    subgraph Repo[Repositories]
        BookRepo[BookingRepository]
        SchedRepo[SchedulingRepository]
        ConvRepo[ConversationRepository]
    end

    subgraph Stores[Persistent Stores]
        Redis[(Redis)]
        Kafka[(Kafka)]
        MySQL[(MySQL)]
        Files[data/*.jsonl + logs/*]
    end

    TG --> WH
    WA1 --> WH
    WA2 --> WH
    WA3 --> WH
    QR --> QRPage
    QR --> QRSubmit

    Main --> WH
    Main --> QRPage
    Main --> QRSubmit
    Main --> Health

    WH --> Dedup
    WH --> Guard
    WH --> Buffer
    WH --> KTurn
    KTurn --> Kafka
    Kafka --> KTurn
    KTurn --> TurnQ

    TurnQ --> Sess
    Sess --> Redis
    Sess --> ConvRepo
    ConvRepo --> MySQL

    TurnQ --> FSM
    FSM --> NLU
    FSM --> LLM
    FSM --> BookRepo
    FSM --> SchedRepo
    BookRepo --> MySQL
    SchedRepo --> MySQL
    SchedRepo --> Redis

    FSM --> Sess
    TurnQ --> Delivery
    Delivery --> TG
    Delivery --> WA1
    Delivery --> WA2
    Delivery --> WA3

    WH --> Logger
    TurnQ --> Logger
    QRSubmit --> Logger
    Logger --> Files
    Dedup --> Files

    QRPage --> QRService
    QRSubmit --> QRService
    QRService --> BookRepo
    QRService --> SchedRepo
    QRService --> MySQL

    Main --> Scheduler
    Main --> OverflowPoll
    Main --> CachePoll

    OverflowPoll --> ConvRepo
    OverflowPoll --> KTurn

    CachePoll --> SchedRepo
    CachePoll --> Redis
    CachePoll --> MySQL

    Scheduler --> BookRepo
    Scheduler --> KNotif
    KNotif --> Kafka
    Kafka --> KNotif
    KNotif --> Delivery
```

## Data Flow Summary

### 1. Telegram / WhatsApp turn flow
- Channel hits webhook route in [webhooks.py](c:\Users\91832\Desktop\Message_bot\src\api\webhooks.py).
- Runtime does dedup, per-user guard, and buffering.
- Turn is published through [kafka_turn_bridge.py](c:\Users\91832\Desktop\Message_bot\src\runtime\kafka_turn_bridge.py).
- Consumer feeds [turn_queue.py](c:\Users\91832\Desktop\Message_bot\src\runtime\turn_queue.py).
- Worker loads session from Redis first, then MySQL fallback via [session_store.py](c:\Users\91832\Desktop\Message_bot\src\session_store.py).
- [appointment_fsm.py](c:\Users\91832\Desktop\Message_bot\src\fsm\appointment_fsm.py) uses:
  - NLU
  - optional LLM routing/extraction
  - booking/scheduling repositories
- Reply goes out through [channel_delivery.py](c:\Users\91832\Desktop\Message_bot\src\runtime\channel_delivery.py).
- Updated session is saved back to Redis and MySQL.

### 2. QR page flow
- Browser opens `GET /qr/checkin`.
- [QrCheckinService](c:\Users\91832\Desktop\Message_bot\src\qr\checkin_service.py) resolves doctor/clinic labels.
- Browser submits name + phone to `POST /qr/checkin/submit`.
- QR service checks:
  - same-day active booking
  - first available same-day slot
  - QR overflow booking if regular slots are full
- QR writes through booking/scheduling repositories into MySQL.
- QR does not use the chat FSM session loop.

### 3. Background automation flow
- [AutomationScheduler](c:\Users\91832\Desktop\Message_bot\src\automation\scheduler.py) polls reminder/notification work from MySQL.
- Notification events are published to Kafka through [kafka_notification_bridge.py](c:\Users\91832\Desktop\Message_bot\src\runtime\kafka_notification_bridge.py).
- Notification consumer sends outbound messages/documents through `ChannelDelivery`.
- Overflow turn poller reads overflow turns from MySQL and hands them back into the Kafka turn bridge.

### 4. Current Redis vs Kafka split
- Redis:
  - session snapshots
  - doctor availability cache
  - processing locks
  - busy hints
- Kafka:
  - inbound turn transport
  - overflow turn handoff
  - notification event transport
- MySQL:
  - patients
  - appointment / appointments
  - slots / doctor_clinic_schedule
  - sessions
  - dedup / overflow / notification / reminder tables
