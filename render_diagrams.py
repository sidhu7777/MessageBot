from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parent


def _add_box(ax, x, y, w, h, text, *, fc="#f4f1ff", ec="#8b7fd1", fontsize=10, weight="normal"):
    patch = FancyBboxPatch(
        (x - w / 2, y - h / 2),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.5,
        facecolor=fc,
        edgecolor=ec,
    )
    ax.add_patch(patch)
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, weight=weight, color="#1f2330")


def _add_arrow(ax, x1, y1, x2, y2, label="", *, color="#666a73", rad=0.0, fontsize=8):
    arrow = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle="-|>",
        mutation_scale=12,
        linewidth=1.3,
        color=color,
        connectionstyle=f"arc3,rad={rad}",
        shrinkA=8,
        shrinkB=8,
    )
    ax.add_patch(arrow)
    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        ax.text(mx, my + (0.18 if rad >= 0 else -0.18), label, fontsize=fontsize, color="#444")


def render_fsm():
    fig, ax = plt.subplots(figsize=(10, 14), dpi=180)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 16)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    nodes = {
        "start": (5.0, 15.2, 1.1, 0.6, "Start"),
        "init": (5.0, 14.0, 1.8, 0.75, "INIT"),
        "booking_for": (5.0, 11.9, 2.3, 0.85, "ASK_BOOKING_FOR"),
        "name": (5.0, 10.1, 2.0, 0.82, "ASK_NAME"),
        "phone": (5.0, 8.4, 2.0, 0.82, "ASK_PHONE"),
        "clinic": (5.0, 6.7, 2.0, 0.82, "ASK_CLINIC"),
        "date": (5.0, 5.0, 2.0, 0.82, "ASK_DATE"),
        "time": (5.0, 3.3, 2.0, 0.82, "ASK_TIME"),
        "confirm": (5.0, 1.7, 1.9, 0.82, "CONFIRM"),
        "completed": (5.0, 0.45, 2.1, 0.82, "COMPLETED"),

        "avail_date": (8.35, 12.9, 2.7, 0.85, "ASK_AVAILABILITY_DATE"),
        "avail_details": (8.35, 10.95, 3.0, 0.9, "ASK_AVAILABILITY_DETAILS"),
        "existing_action": (8.35, 6.85, 3.0, 0.95, "ASK_EXISTING_\nBOOKING_ACTION"),
        "pick": (8.35, 4.95, 2.8, 0.9, "ASK_EXISTING_\nBOOKING_PICK"),
        "confirm_reschedule": (8.35, 3.05, 3.0, 0.95, "CONFIRM_RESCHEDULE"),

        "max_active": (1.8, 6.1, 2.7, 0.95, "ASK_MAX_ACTIVE_\nBOOKINGS_ACTION"),
        "change": (1.8, 2.6, 2.5, 0.9, "ASK_CHANGE_FIELD"),
        "cancelled": (1.8, 0.45, 2.0, 0.82, "CANCELLED"),
    }

    for key, (x, y, w, h, label) in nodes.items():
        fc = "#efeaff"
        ec = "#8d7be8"
        if key in {"completed", "cancelled"}:
            fc = "#eaf8ee"
            ec = "#4a9b68"
        elif key in {"init", "start"}:
            fc = "#ecebfd"
            ec = "#7e78cc"
        _add_box(ax, x, y, w, h, label, fc=fc, ec=ec, fontsize=10, weight="bold" if key in {"init", "completed"} else "normal")

    _add_arrow(ax, 5.0, 14.8, 5.0, 14.4)
    _add_arrow(ax, 5.0, 13.55, 5.0, 12.35)
    _add_arrow(ax, 5.0, 11.45, 5.0, 10.55)
    _add_arrow(ax, 5.0, 9.65, 5.0, 8.85)
    _add_arrow(ax, 5.0, 7.95, 5.0, 7.15)
    _add_arrow(ax, 5.0, 6.25, 5.0, 5.45)
    _add_arrow(ax, 5.0, 4.55, 5.0, 3.75)
    _add_arrow(ax, 5.0, 2.85, 5.0, 2.15)
    _add_arrow(ax, 5.0, 1.25, 5.0, 0.8)

    _add_arrow(ax, 5.9, 13.9, 7.0, 12.95, rad=-0.08)
    _add_arrow(ax, 8.35, 12.45, 8.35, 11.45)
    _add_arrow(ax, 7.6, 10.85, 5.9, 11.75, rad=0.03)

    _add_arrow(ax, 5.95, 6.8, 7.0, 6.95)
    _add_arrow(ax, 8.35, 6.4, 8.35, 5.45)
    _add_arrow(ax, 8.35, 4.5, 8.35, 3.5)
    _add_arrow(ax, 7.55, 2.95, 5.85, 1.05, rad=-0.05)

    _add_arrow(ax, 4.05, 6.45, 2.95, 6.15, rad=0.02)
    _add_arrow(ax, 1.8, 5.55, 1.8, 3.1)
    _add_arrow(ax, 2.55, 2.35, 4.15, 1.85)
    _add_arrow(ax, 1.8, 1.95, 1.8, 0.85)

    _add_arrow(ax, 5.55, 0.6, 7.2, 6.35, rad=0.18)
    _add_arrow(ax, 4.45, 0.75, 3.95, 10.35, rad=-0.28)

    ax.text(6.0, 14.15, "hello / hi", fontsize=8, color="#444")
    ax.text(4.0, 13.15, "booking intent / 1", fontsize=8, color="#444")
    ax.text(7.15, 13.85, "availability / 2", fontsize=8, color="#444")
    ax.text(8.95, 11.95, "pick date", fontsize=8, color="#444")
    ax.text(6.55, 11.35, "book from availability", fontsize=8, color="#444")
    ax.text(6.1, 10.55, "known / unknown self", fontsize=8, color="#444")
    ax.text(6.1, 7.4, "existing booking", fontsize=8, color="#444")
    ax.text(8.9, 5.85, "multiple bookings", fontsize=8, color="#444")
    ax.text(8.9, 4.0, "reschedule", fontsize=8, color="#444")
    ax.text(0.9, 4.45, "change / limits", fontsize=8, color="#444")
    ax.text(7.15, 6.1, "keep / cancel /\nreschedule", fontsize=8, color="#444", ha="left")
    ax.text(6.85, 1.75, "confirm reschedule", fontsize=8, color="#444")
    ax.text(3.35, 8.0, "restart / new booking", fontsize=8, color="#444", rotation=82)

    ax.set_title("Current Appointment FSM Flow", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Current_FSM_Flow.png", bbox_inches="tight")
    fig.savefig(ROOT / "Current_FSM_Flow.svg", bbox_inches="tight")
    plt.close(fig)


def render_architecture():
    fig, ax = plt.subplots(figsize=(18, 10), dpi=180)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 14)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    groups = [
        (1.8, 7.0, 2.6, 10.6, "Channels", "#f4fbff", "#93bdd4"),
        (5.0, 7.0, 2.6, 10.6, "FastAPI", "#f7f4ff", "#b29ada"),
        (8.6, 7.0, 3.8, 10.8, "Runtime", "#f8f9fb", "#98a2b3"),
        (12.7, 7.0, 3.4, 10.8, "Services / Async", "#f7fff7", "#8ebd8a"),
        (16.2, 7.0, 3.0, 10.8, "Repositories / Stores", "#fff8f1", "#d8b184"),
    ]
    for x, y, w, h, label, fc, ec in groups:
        patch = FancyBboxPatch(
            (x - w / 2, y - h / 2),
            w,
            h,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            linewidth=1.6,
            facecolor=fc,
            edgecolor=ec,
            alpha=0.55,
        )
        ax.add_patch(patch)
        ax.text(x, y + h / 2 - 0.4, label, ha="center", va="center", fontsize=13, weight="bold", color="#223")

    nodes = {
        "tg": (1.8, 11.5, 1.8, 0.8, "Telegram"),
        "wa": (1.8, 9.8, 1.9, 1.0, "WhatsApp\nTwilio / Meta /\nInfobip"),
        "qr": (1.8, 8.0, 1.9, 0.8, "QR / Web"),

        "routes": (5.0, 11.0, 1.9, 0.9, "Webhook routes"),
        "qrpage": (5.0, 9.0, 2.0, 0.85, "QR page routes"),
        "health": (5.0, 7.0, 1.9, 0.8, "Health"),

        "dedup": (7.5, 11.0, 1.8, 0.8, "SID dedup"),
        "guard": (9.8, 11.0, 2.0, 0.8, "User guard +\nbuffer"),
        "kturn": (7.5, 9.3, 1.9, 0.85, "Kafka turn\nbridge"),
        "turnq": (9.8, 9.3, 2.0, 0.85, "Turn queue"),
        "sess": (7.5, 7.6, 1.8, 0.8, "Session\nmanager"),
        "fsm": (9.8, 7.6, 1.9, 0.8, "Appointment\nFSM"),
        "delivery": (8.65, 5.6, 2.1, 0.85, "Channel delivery"),
        "logger": (8.65, 3.8, 1.8, 0.75, "Chat logger"),

        "qrsvc": (12.7, 10.8, 2.0, 0.85, "QR check-in\nservice"),
        "llm": (12.7, 9.1, 1.8, 0.8, "LLM client +\nLLM tasks"),
        "nlu": (12.7, 7.4, 1.8, 0.8, "NLU router +\nextractors"),
        "sched": (12.7, 5.7, 2.0, 0.85, "Automation\nscheduler"),
        "knotif": (12.7, 4.0, 2.0, 0.85, "Kafka notif\nbridge"),
        "bg": (12.7, 2.3, 2.2, 0.95, "Overflow poller +\ncache invalidation"),

        "bookrepo": (15.2, 10.8, 1.9, 0.85, "Booking\nrepository"),
        "schedrepo": (17.1, 10.8, 1.9, 0.85, "Scheduling\nrepository"),
        "convrepo": (16.15, 9.0, 2.0, 0.85, "Conversation\nrepository"),
        "redis": (15.2, 6.3, 1.6, 0.8, "Redis"),
        "kafka": (17.1, 6.3, 1.6, 0.8, "Kafka"),
        "mysql": (16.15, 4.5, 1.8, 0.8, "MySQL"),
        "files": (16.15, 2.5, 1.8, 0.8, "Logs + JSONL"),
    }

    for key, (x, y, w, h, label) in nodes.items():
        fc = "#ffffff"
        ec = "#7f8ea3"
        if key in {"redis", "kafka", "mysql", "files"}:
            fc = "#fff7ea"
            ec = "#c49553"
        elif key in {"bookrepo", "schedrepo", "convrepo"}:
            fc = "#fffaf2"
            ec = "#d1a86d"
        elif key in {"qrsvc", "llm", "nlu", "sched", "knotif", "bg"}:
            fc = "#f5fff5"
            ec = "#7bab76"
        elif key in {"routes", "qrpage", "health"}:
            fc = "#f4efff"
            ec = "#a48ada"
        elif key in {"kturn", "turnq", "sess", "fsm", "delivery"}:
            fc = "#f7f8fb"
            ec = "#97a0af"
        _add_box(ax, x, y, w, h, label, fc=fc, ec=ec, fontsize=10, weight="bold" if key in {"fsm", "kafka", "mysql"} else "normal")

    _add_arrow(ax, 2.7, 11.5, 4.0, 11.0)
    _add_arrow(ax, 2.75, 9.8, 4.0, 11.0)
    _add_arrow(ax, 2.75, 8.0, 4.0, 9.0, "GET / POST")

    _add_arrow(ax, 6.0, 11.0, 6.6, 11.0)
    _add_arrow(ax, 8.4, 11.0, 8.8, 11.0)
    _add_arrow(ax, 7.5, 10.85, 7.5, 9.75, "publish")
    _add_arrow(ax, 8.45, 9.3, 8.85, 9.3, "consume")
    _add_arrow(ax, 7.5, 8.9, 7.5, 8.05)
    _add_arrow(ax, 8.4, 7.6, 8.85, 7.6)
    _add_arrow(ax, 9.8, 7.2, 8.75, 5.95)
    _add_arrow(ax, 9.25, 5.6, 2.85, 11.55, "outbound replies", rad=-0.34)

    _add_arrow(ax, 6.0, 9.0, 11.6, 10.8, "doctor / clinic resolve")
    _add_arrow(ax, 6.0, 8.85, 11.6, 10.5, "submit booking", rad=-0.04)

    _add_arrow(ax, 10.75, 7.9, 11.8, 9.1, "LLM fallback", rad=0.06)
    _add_arrow(ax, 10.75, 7.45, 11.8, 7.45, "routing / extraction")
    _add_arrow(ax, 10.75, 7.75, 14.2, 10.8, "booking queries", rad=0.02)
    _add_arrow(ax, 10.75, 7.55, 16.0, 10.8, "availability queries", rad=-0.02)

    _add_arrow(ax, 7.95, 7.45, 15.1, 8.95, "DB session load / save", rad=0.03)
    _add_arrow(ax, 7.55, 7.25, 15.1, 6.45, "Redis snapshot / cache", rad=-0.04)

    _add_arrow(ax, 14.7, 10.8, 16.1, 10.8)
    _add_arrow(ax, 13.7, 5.7, 14.35, 10.6, "claim reminders", rad=0.08)
    _add_arrow(ax, 12.7, 5.25, 12.7, 4.45, "publish")
    _add_arrow(ax, 13.55, 4.0, 16.15, 6.3, "notify via Kafka", rad=-0.05)
    _add_arrow(ax, 13.8, 2.4, 16.15, 9.0, "overflow / invalidation", rad=0.04)

    _add_arrow(ax, 15.2, 10.35, 16.15, 4.9)
    _add_arrow(ax, 17.1, 10.35, 16.25, 4.9)
    _add_arrow(ax, 16.15, 8.55, 16.15, 4.95)
    _add_arrow(ax, 15.2, 5.85, 15.7, 4.8)
    _add_arrow(ax, 17.1, 5.85, 16.6, 4.8)
    _add_arrow(ax, 16.15, 4.05, 16.15, 2.9)

    ax.set_title("Current Model Architecture And Data Flow", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Current_Model_Architecture.png", bbox_inches="tight")
    fig.savefig(ROOT / "Current_Model_Architecture.svg", bbox_inches="tight")
    plt.close(fig)


def render_fsm_presentation():
    fig, ax = plt.subplots(figsize=(17, 9), dpi=180)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 11)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    def box(name, x, y, w=2.2, h=0.9, fc="#efeaff", ec="#8d7be8", bold=False):
        _add_box(ax, x, y, w, h, name, fc=fc, ec=ec, fontsize=10, weight="bold" if bold else "normal")

    box("Start", 1.6, 9.7, 1.6, 0.7)
    box("INIT", 4.0, 9.7, 2.0, 0.8, bold=True)
    box("Greeting +\nchoose 1 or 2", 7.0, 9.7, 2.8, 0.95)
    box("ASK_AVAILABILITY_DATE", 10.5, 9.7, 2.9, 0.9)
    box("ASK_AVAILABILITY_DETAILS", 13.9, 9.7, 3.0, 0.95)

    box("ASK_BOOKING_FOR", 4.0, 7.2, 2.5, 0.9)
    box("ASK_NAME", 7.0, 7.2)
    box("ASK_PHONE", 10.0, 7.2)
    box("ASK_CLINIC", 13.0, 7.2)
    box("ASK_DATE", 4.0, 4.7)
    box("ASK_TIME", 7.0, 4.7)
    box("CONFIRM", 10.0, 4.7)
    box("COMPLETED", 13.0, 4.7, fc="#eaf8ee", ec="#4a9b68", bold=True)

    box("ASK_EXISTING_BOOKING_ACTION", 4.5, 2.2, 3.1, 0.95)
    box("ASK_EXISTING_BOOKING_PICK", 8.2, 2.2, 3.0, 0.95)
    box("ASK_MAX_ACTIVE_BOOKINGS_ACTION", 11.9, 2.2, 3.2, 0.95)
    box("CONFIRM_RESCHEDULE", 15.5, 2.2, 3.0, 0.95)

    _add_arrow(ax, 2.4, 9.7, 3.0, 9.7)
    _add_arrow(ax, 5.0, 9.7, 5.9, 9.7, "hello / hi")
    _add_arrow(ax, 4.0, 9.25, 4.0, 7.65, "1 or booking intent")
    _add_arrow(ax, 5.0, 9.7, 9.0, 9.7, "2 or availability")
    _add_arrow(ax, 11.95, 9.7, 12.4, 9.7, "pick date")
    _add_arrow(ax, 15.1, 9.25, 4.8, 7.45, "booking intent / restart / 1", rad=0.02)

    _add_arrow(ax, 5.25, 7.2, 5.9, 7.2, "another person")
    _add_arrow(ax, 4.45, 6.9, 6.1, 6.9, "self if unknown", rad=0.0)
    _add_arrow(ax, 4.9, 7.55, 9.0, 7.55, "self if known + phone")
    _add_arrow(ax, 4.85, 7.35, 8.9, 7.3, "self if phone missing", rad=-0.05)

    _add_arrow(ax, 8.1, 7.2, 8.9, 7.2, "valid name")
    _add_arrow(ax, 11.1, 7.2, 11.9, 7.2, "valid / same number")
    _add_arrow(ax, 14.1, 7.2, 5.0, 4.85, "pick clinic", rad=0.05)
    _add_arrow(ax, 5.1, 4.7, 5.9, 4.7, "pick date")
    _add_arrow(ax, 8.1, 4.7, 8.9, 4.7, "pick time")
    _add_arrow(ax, 11.1, 4.7, 11.9, 4.7, "confirm")

    _add_arrow(ax, 4.0, 9.25, 4.4, 2.65, "existing booking", rad=-0.2)
    _add_arrow(ax, 6.05, 2.2, 6.8, 2.2, "multiple bookings")
    _add_arrow(ax, 6.05, 2.45, 12.1, 4.45, "single reschedule", rad=0.08)
    _add_arrow(ax, 8.2, 2.65, 14.2, 2.65, "reschedule selected", rad=0.04)
    _add_arrow(ax, 8.2, 1.95, 12.4, 4.4, "cancel selected", rad=-0.04)
    _add_arrow(ax, 13.55, 2.2, 14.0, 2.2, "reschedule / cancel existing")
    _add_arrow(ax, 15.5, 2.65, 13.3, 4.45, "confirm", rad=0.04)

    ax.text(1.0, 8.7, "Entry / availability", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 6.2, "New booking path", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 1.2, "Existing booking / reschedule path", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 0.5, "Note: back arrows, invalid-input loops, and CANCELLED/CHANGE detail are omitted here for readability.\nThe exact state coverage remains in Flowchart.md and flowchart.mmd.", fontsize=9, color="#555")

    ax.set_title("Conversation Flow (Presentation Version)", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Conversation_Flow_Presentation.png", bbox_inches="tight")
    fig.savefig(ROOT / "Conversation_Flow_Presentation.svg", bbox_inches="tight")
    plt.close(fig)


def render_code_responsibility():
    fig, ax = plt.subplots(figsize=(19, 11), dpi=180)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 13)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    groups = [
        (2.6, 6.2, 3.8, 10.0, "Entry / API", "#f4fbff", "#93bdd4"),
        (7.2, 6.2, 4.2, 10.0, "Conversation Core", "#f7f4ff", "#b29ada"),
        (12.0, 6.2, 3.8, 10.0, "Runtime / Background", "#f7fff7", "#8ebd8a"),
        (16.0, 6.2, 3.2, 10.0, "Persistence / External", "#fff8f1", "#d8b184"),
    ]
    for x, y, w, h, label, fc, ec in groups:
        patch = FancyBboxPatch(
            (x - w / 2, y - h / 2),
            w,
            h,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            linewidth=1.6,
            facecolor=fc,
            edgecolor=ec,
            alpha=0.55,
        )
        ax.add_patch(patch)
        ax.text(x, y + h / 2 - 0.15, label, ha="center", va="center", fontsize=13, weight="bold", color="#223")

    def module_box(x, y, title, desc, *, fc, ec):
        _add_box(ax, x, y, 3.15, 1.32, f"{title}\n{desc}", fc=fc, ec=ec, fontsize=8.6)

    module_box(2.6, 10.6, "main.py", "startup wiring\nFastAPI routes\nscheduler + services", fc="#eef8ff", ec="#8dbbd7")
    module_box(2.6, 8.8, "src/api/webhooks.py", "Telegram / WhatsApp\nwebhook parsing\ndedup + queue handoff", fc="#eef8ff", ec="#8dbbd7")
    module_box(2.6, 6.6, "src/qr/checkin_service.py", "QR page submit logic\nsame-day booking\nQR overflow rules", fc="#eef8ff", ec="#8dbbd7")
    module_box(2.6, 4.4, "src/config.py", "environment config\nprovider flags\nruntime settings", fc="#eef8ff", ec="#8dbbd7")

    module_box(7.2, 10.6, "src/session_store.py", "load/save FSM session\nRedis first\nDB fallback", fc="#f5f0ff", ec="#a78dd8")
    module_box(7.2, 8.8, "src/fsm/appointment_fsm.py", "state machine core\nstate dispatch\nshared helpers", fc="#f5f0ff", ec="#a78dd8")
    module_box(7.2, 6.6, "src/fsm/handlers/*", "INIT / booking /\nexisting booking /\navailability handlers", fc="#f5f0ff", ec="#a78dd8")
    module_box(7.2, 4.4, "src/nlu/* + src/llm/*", "intent routing\nentity extraction\nLLM fallback tasks", fc="#f5f0ff", ec="#a78dd8")

    module_box(12.0, 10.6, "src/runtime/turn_queue.py", "worker queue\nretry / timeout\ntask processing", fc="#f3fff3", ec="#7cab78")
    module_box(12.0, 8.8, "src/runtime/kafka_*.py", "Kafka turn bridge\nKafka notif bridge\nasync transport", fc="#f3fff3", ec="#7cab78")
    module_box(12.0, 6.6, "src/runtime/channel_delivery.py", "send replies via\nTelegram / Twilio /\nMeta / Infobip", fc="#f3fff3", ec="#7cab78")
    module_box(12.0, 4.4, "src/automation/scheduler.py\nsrc/runtime/background_workers.py", "doctor reminders\nnotification processing\noverflow + cache workers", fc="#f3fff3", ec="#7cab78")

    module_box(16.0, 10.6, "src/repositories/booking_repository.py", "patient + appointment\nqueries and writes\nnotifications / reminders", fc="#fff9f0", ec="#d1a76d")
    module_box(16.0, 8.8, "src/repositories/scheduling_repository.py", "clinic/date/time\navailability lookup\nRedis availability cache", fc="#fff9f0", ec="#d1a76d")
    module_box(16.0, 6.6, "src/repositories/conversation_repository.py", "conversation sessions\nmessage dedup table\noverflow turn queue", fc="#fff9f0", ec="#d1a76d")
    module_box(16.0, 4.4, "src/db/connection.py\nsrc/db_store.py", "MySQL pool config\nrepo construction\nDB entry layer", fc="#fff9f0", ec="#d1a76d")

    _add_arrow(ax, 4.2, 10.6, 5.55, 10.6)
    _add_arrow(ax, 4.2, 8.8, 10.25, 8.8)
    _add_arrow(ax, 4.2, 8.55, 5.6, 8.55, rad=0.0)
    _add_arrow(ax, 8.8, 8.8, 10.45, 8.8)
    _add_arrow(ax, 7.2, 8.1, 7.2, 7.3)
    _add_arrow(ax, 8.8, 6.75, 14.35, 11.0, rad=0.01)
    _add_arrow(ax, 8.8, 6.55, 14.35, 8.8, rad=-0.02)
    _add_arrow(ax, 8.8, 4.55, 14.35, 8.55, rad=-0.01)
    _add_arrow(ax, 12.0, 7.95, 12.0, 7.45)
    _add_arrow(ax, 13.55, 6.55, 14.35, 4.55, rad=-0.04)
    _add_arrow(ax, 13.6, 10.6, 14.45, 10.6)
    _add_arrow(ax, 14.45, 6.6, 14.6, 6.6)
    _add_arrow(ax, 14.45, 8.8, 14.6, 8.8)
    _add_arrow(ax, 14.45, 10.6, 14.6, 10.6)

    ax.text(1.0, 1.2, "Purpose: show which code area owns which responsibility. This is a code/module view, not a DB ERD or runtime sequence diagram.", fontsize=10, color="#555")
    ax.set_title("Code / Module Responsibility Map", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Code_Module_Responsibility.png", bbox_inches="tight")
    fig.savefig(ROOT / "Code_Module_Responsibility.svg", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    render_fsm()
    render_architecture()
    render_fsm_presentation()
    render_code_responsibility()
