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


def _add_elbow_arrow(ax, points, label="", *, color="#666a73", fontsize=8, label_pos=None):
    for i in range(len(points) - 2):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        ax.plot([x1, x2], [y1, y2], color=color, linewidth=1.3)
    start = points[-2]
    end = points[-1]
    arrow = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=12,
        linewidth=1.3,
        color=color,
        shrinkA=0,
        shrinkB=8,
    )
    ax.add_patch(arrow)
    if label:
        if label_pos is None:
            x1, y1 = points[0]
            x2, y2 = points[-1]
            label_pos = ((x1 + x2) / 2, (y1 + y2) / 2)
        ax.text(label_pos[0], label_pos[1], label, fontsize=fontsize, color="#444")


def render_fsm():
    """Generate a lane-based FSM diagram with routed connectors."""
    fig, ax = plt.subplots(figsize=(18, 13), dpi=180)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 16)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    nodes = {
        "start": (9.0, 14.0, 1.4, 0.65, "Start"),
        "init": (9.0, 12.8, 2.2, 0.9, "INIT"),
        "booking_for": (9.0, 11.1, 2.7, 0.9, "ASK_BOOKING_FOR"),
        "name": (9.0, 9.7, 2.1, 0.85, "ASK_NAME"),
        "phone": (9.0, 8.3, 2.1, 0.85, "ASK_PHONE"),
        "clinic": (9.0, 6.9, 2.1, 0.85, "ASK_CLINIC"),
        "date": (9.0, 5.5, 2.1, 0.85, "ASK_DATE"),
        "time": (9.0, 4.1, 2.1, 0.85, "ASK_TIME"),
        "confirm": (9.0, 2.7, 2.1, 0.85, "CONFIRM"),
        "completed": (9.0, 1.1, 2.4, 0.9, "COMPLETED", "#eaf8ee", "#4a9b68", True),
        "avail_date": (14.8, 12.0, 3.0, 0.9, "ASK_AVAILABILITY_DATE"),
        "avail_details": (14.8, 10.4, 3.2, 0.9, "ASK_AVAILABILITY_DETAILS"),
        "existing_action": (14.8, 7.2, 3.5, 0.95, "ASK_EXISTING_BOOKING_ACTION"),
        "existing_pick": (14.8, 5.5, 3.2, 0.9, "ASK_EXISTING_BOOKING_PICK"),
        "confirm_reschedule": (14.8, 3.8, 3.2, 0.95, "CONFIRM_RESCHEDULE"),
        "max_active": (3.3, 10.4, 3.3, 0.95, "ASK_MAX_ACTIVE_BOOKINGS_ACTION"),
        "change": (3.3, 8.4, 2.8, 0.9, "ASK_CHANGE_FIELD"),
        "cancelled": (3.3, 6.5, 2.4, 0.9, "CANCELLED", "#eaf8ee", "#4a9b68", True),
    }

    def port(key, side):
        x, y, w, h, *_ = nodes[key]
        ports = {
            "top": (x, y + h / 2),
            "bottom": (x, y - h / 2),
            "left": (x - w / 2, y),
            "right": (x + w / 2, y),
        }
        return ports[side]

    for key_data, (x, y, w, h, label, *rest) in nodes.items():
        fc = "#efeaff"
        ec = "#8d7be8"
        bold = False
        if rest:
            fc, ec = rest[0], rest[1]
            if len(rest) > 2:
                bold = rest[2]
        elif key_data in {"init", "start"}:
            fc = "#ecebfd"
            ec = "#7e78cc"
            bold = True
        
        _add_box(ax, x, y, w, h, label, fc=fc, ec=ec, fontsize=10, weight="bold" if bold else "normal")

    _add_arrow(ax, *port("start", "bottom"), *port("init", "top"), fontsize=8)
    _add_arrow(ax, *port("init", "bottom"), *port("booking_for", "top"), fontsize=8)
    _add_arrow(ax, *port("booking_for", "bottom"), *port("name", "top"), fontsize=8)
    _add_arrow(ax, *port("name", "bottom"), *port("phone", "top"), fontsize=8)
    _add_arrow(ax, *port("phone", "bottom"), *port("clinic", "top"), fontsize=8)
    _add_arrow(ax, *port("clinic", "bottom"), *port("date", "top"), fontsize=8)
    _add_arrow(ax, *port("date", "bottom"), *port("time", "top"), fontsize=8)
    _add_arrow(ax, *port("time", "bottom"), *port("confirm", "top"), fontsize=8)
    _add_arrow(ax, *port("confirm", "bottom"), *port("completed", "top"), fontsize=8)

    _add_elbow_arrow(
        ax,
        [port("init", "right"), (11.5, port("init", "right")[1]), (13.0, port("avail_date", "left")[1]), port("avail_date", "left")],
        fontsize=8,
    )
    _add_arrow(ax, *port("avail_date", "bottom"), *port("avail_details", "top"), fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("avail_details", "left"), (12.2, port("avail_details", "left")[1]), (11.2, port("booking_for", "right")[1]), port("booking_for", "right")],
        fontsize=8,
    )

    _add_elbow_arrow(
        ax,
        [port("init", "right"), (16.7, port("init", "right")[1]), (16.7, port("existing_action", "top")[1] + 0.5), port("existing_action", "top")],
        fontsize=8,
    )
    _add_arrow(ax, *port("existing_action", "bottom"), *port("existing_pick", "top"), fontsize=8)
    _add_arrow(ax, *port("existing_pick", "bottom"), *port("confirm_reschedule", "top"), fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("confirm_reschedule", "left"), (12.3, port("confirm_reschedule", "left")[1]), (11.0, port("completed", "right")[1]), port("completed", "right")],
        fontsize=8,
    )
    _add_elbow_arrow(
        ax,
        [port("existing_pick", "left"), (12.6, port("existing_pick", "left")[1]), (11.4, port("completed", "right")[1]), port("completed", "right")],
        fontsize=8,
    )

    _add_elbow_arrow(
        ax,
        [port("booking_for", "left"), (5.7, port("booking_for", "left")[1]), (5.7, port("max_active", "right")[1]), port("max_active", "right")],
        fontsize=8,
    )
    _add_arrow(ax, *port("max_active", "bottom"), *port("change", "top"), fontsize=8)
    _add_arrow(ax, *port("change", "bottom"), *port("cancelled", "top"), fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("change", "right"), (5.9, port("change", "right")[1]), (7.4, port("clinic", "left")[1]), port("clinic", "left")],
        fontsize=8,
    )

    _add_elbow_arrow(
        ax,
        [port("completed", "left"), (6.1, port("completed", "left")[1]), (6.1, 12.0), (7.9, 12.0), (7.9, port("init", "left")[1]), port("init", "left")],
        fontsize=8,
    )

    ax.text(9, 14.65, "Appointment Booking FSM - Updated And Clean Layout", fontsize=14, weight="bold", ha="center")
    ax.set_title("Current Appointment FSM Flow", fontsize=16, weight="bold", pad=12)
    fig.tight_layout()
    fig.savefig(ROOT / "Current_FSM_Flow.png", bbox_inches="tight", dpi=150)
    fig.savefig(ROOT / "Current_FSM_Flow.svg", bbox_inches="tight")
    plt.close(fig)
    print("Generated clean FSM diagram: Current_FSM_Flow.png (.svg)")




def render_architecture():
    fig, ax = plt.subplots(figsize=(19, 11), dpi=180)
    ax.set_xlim(0, 19)
    ax.set_ylim(0, 15)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    groups = [
        (1.9, 7.4, 2.8, 11.0, "Channels", "#f4fbff", "#93bdd4"),
        (5.3, 7.4, 2.8, 11.0, "FastAPI", "#f7f4ff", "#b29ada"),
        (9.3, 7.4, 4.2, 11.2, "Runtime", "#f8f9fb", "#98a2b3"),
        (13.6, 7.4, 3.8, 11.2, "Services / Async", "#f7fff7", "#8ebd8a"),
        (17.3, 7.4, 3.2, 11.2, "Repositories / Stores", "#fff8f1", "#d8b184"),
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
        "tg": (1.9, 12.0, 2.0, 0.82, "Telegram"),
        "wa": (1.9, 10.2, 2.0, 1.05, "WhatsApp\nTwilio / Meta /\nInfobip"),
        "qr": (1.9, 8.2, 2.0, 0.82, "QR / Web"),

        "routes": (5.3, 11.3, 2.0, 0.9, "Webhook routes"),
        "qrpage": (5.3, 9.1, 2.1, 0.9, "QR page routes"),
        "health": (5.3, 6.9, 2.0, 0.82, "Health"),

        "dedup": (8.1, 11.3, 1.9, 0.82, "SID dedup"),
        "guard": (10.4, 11.3, 2.2, 0.82, "User guard +\nbuffer"),
        "kturn": (8.1, 9.2, 2.0, 0.9, "Kafka turn\nbridge"),
        "turnq": (10.4, 9.2, 2.2, 0.9, "Turn queue"),
        "sess": (8.1, 7.1, 1.9, 0.82, "Session\nmanager"),
        "fsm": (10.4, 7.1, 2.2, 0.82, "Appointment\nFSM"),
        "delivery": (9.2, 4.9, 2.4, 0.92, "Channel delivery"),
        "logger": (9.2, 2.9, 1.9, 0.8, "Chat logger"),

        "qrsvc": (13.6, 11.0, 2.3, 0.9, "QR check-in\nservice"),
        "llm": (13.6, 8.9, 2.2, 0.85, "LLM client +\nLLM tasks"),
        "nlu": (13.6, 6.8, 2.2, 0.85, "NLU router +\nextractors"),
        "sched": (13.6, 4.7, 2.3, 0.9, "Automation\nscheduler"),
        "knotif": (13.6, 2.8, 2.3, 0.9, "Kafka notif\nbridge"),

        "bookrepo": (16.3, 11.0, 2.1, 0.9, "Booking\nrepository"),
        "schedrepo": (18.1, 11.0, 2.1, 0.9, "Scheduling\nrepository"),
        "convrepo": (17.2, 8.9, 2.2, 0.9, "Conversation\nrepository"),
        "redis": (16.3, 6.1, 1.8, 0.82, "Redis"),
        "kafka": (18.1, 6.1, 1.8, 0.82, "Kafka"),
        "mysql": (17.2, 4.0, 2.0, 0.82, "MySQL"),
        "files": (17.2, 2.0, 2.0, 0.82, "Logs + JSONL"),
    }

    def port(key, side):
        x, y, w, h, _ = nodes[key]
        return {
            "top": (x, y + h / 2),
            "bottom": (x, y - h / 2),
            "left": (x - w / 2, y),
            "right": (x + w / 2, y),
        }[side]

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

    _add_arrow(ax, *port("tg", "right"), *port("routes", "left"))
    _add_arrow(ax, *port("wa", "right"), *port("routes", "left"))
    _add_arrow(ax, *port("qr", "right"), *port("qrpage", "left"), "GET / POST")

    _add_arrow(ax, *port("routes", "right"), *port("dedup", "left"))
    _add_arrow(ax, *port("dedup", "right"), *port("guard", "left"))
    _add_arrow(ax, *port("dedup", "bottom"), *port("kturn", "top"), "publish")
    _add_arrow(ax, *port("kturn", "right"), *port("turnq", "left"), "consume")
    _add_arrow(ax, *port("turnq", "bottom"), *port("fsm", "top"))
    _add_arrow(ax, *port("sess", "right"), *port("fsm", "left"))
    _add_arrow(ax, *port("fsm", "bottom"), *port("delivery", "top"))
    _add_arrow(ax, *port("delivery", "bottom"), *port("logger", "top"))

    _add_elbow_arrow(
        ax,
        [port("qrpage", "right"), (6.6, port("qrpage", "right")[1]), (6.6, 15.0), (13.6, 15.0), port("qrsvc", "top")],
        "resolve / submit",
        fontsize=8,
        label_pos=(9.3, 15.25),
    )

    _add_arrow(ax, *port("fsm", "right"), *port("nlu", "left"), "route / extract")
    _add_elbow_arrow(
        ax,
        [port("fsm", "right"), (11.4, port("fsm", "right")[1]), (11.4, port("llm", "left")[1]), port("llm", "left")],
        "LLM fallback",
        fontsize=8,
        label_pos=(11.5, 8.45),
    )
    _add_elbow_arrow(
        ax,
        [port("fsm", "right"), (11.6, port("fsm", "right")[1]), (11.6, 15.45), (16.3, 15.45), port("bookrepo", "top")],
        "booking lookups",
        fontsize=8,
        label_pos=(13.3, 15.7),
    )
    _add_elbow_arrow(
        ax,
        [port("fsm", "right"), (11.1, port("fsm", "right")[1]), (11.1, 14.8), (18.1, 14.8), port("schedrepo", "top")],
        "availability lookups",
        fontsize=8,
        label_pos=(14.8, 15.05),
    )
    _add_elbow_arrow(
        ax,
        [port("sess", "left"), (7.0, port("sess", "left")[1]), (7.0, 14.2), (17.2, 14.2), port("convrepo", "top")],
        "session save / load",
        fontsize=8,
        label_pos=(13.3, 14.45),
    )
    _add_elbow_arrow(
        ax,
        [port("sess", "left"), (7.1, 7.1), (7.1, 5.55), (15.4, 5.55), port("redis", "bottom")],
        "cache snapshot",
        fontsize=8,
        label_pos=(11.0, 5.8),
    )

    _add_arrow(ax, *port("qrsvc", "right"), *port("bookrepo", "left"))
    _add_arrow(ax, *port("bookrepo", "right"), *port("schedrepo", "left"))
    _add_arrow(ax, *port("sched", "bottom"), *port("knotif", "top"), "publish")

    _add_arrow(ax, *port("bookrepo", "bottom"), *port("mysql", "top"))
    _add_arrow(ax, *port("schedrepo", "bottom"), *port("mysql", "top"))
    _add_arrow(ax, *port("convrepo", "bottom"), *port("mysql", "top"))
    _add_arrow(ax, *port("redis", "bottom"), *port("mysql", "top"))
    _add_arrow(ax, *port("kafka", "bottom"), *port("mysql", "top"))
    _add_arrow(ax, *port("mysql", "bottom"), *port("files", "top"))

    ax.text(2.8, 13.6, "Inbound channels", fontsize=10, color="#555", weight="bold")
    ax.text(8.8, 13.6, "Core runtime flow", fontsize=10, color="#555", weight="bold")
    ax.text(13.4, 13.6, "Supporting services", fontsize=10, color="#555", weight="bold")
    ax.text(16.0, 13.6, "Persistence", fontsize=10, color="#555", weight="bold")

    ax.set_title("Current Model Architecture And Data Flow", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Current_Model_Architecture.png", bbox_inches="tight")
    fig.savefig(ROOT / "Current_Model_Architecture.svg", bbox_inches="tight")
    plt.close(fig)


def render_fsm_presentation():
    fig, ax = plt.subplots(figsize=(18, 10), dpi=180)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 12)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    boxes = {}

    def box(key, name, x, y, w=2.2, h=0.9, fc="#efeaff", ec="#8d7be8", bold=False):
        boxes[key] = (x, y, w, h)
        _add_box(ax, x, y, w, h, name, fc=fc, ec=ec, fontsize=10, weight="bold" if bold else "normal")

    def port(key, side):
        x, y, w, h = boxes[key]
        return {
            "top": (x, y + h / 2),
            "bottom": (x, y - h / 2),
            "left": (x - w / 2, y),
            "right": (x + w / 2, y),
        }[side]

    box("start", "Start", 1.6, 10.2, 1.7, 0.75)
    box("init", "INIT", 4.1, 10.2, 2.1, 0.85, bold=True)
    box("greeting", "Greeting +\nchoose 1 or 2", 7.2, 10.2, 3.0, 1.0)
    box("avail_date", "ASK_AVAILABILITY_DATE", 11.1, 10.2, 3.0, 0.9)
    box("avail_details", "ASK_AVAILABILITY_DETAILS", 15.1, 10.2, 3.3, 0.95)

    box("booking_for", "ASK_BOOKING_FOR", 4.1, 7.1, 2.6, 0.95)
    box("name", "ASK_NAME", 7.4, 7.1, 2.3, 0.9)
    box("phone", "ASK_PHONE", 10.5, 7.1, 2.3, 0.9)
    box("clinic", "ASK_CLINIC", 13.6, 7.1, 2.3, 0.9)
    box("date", "ASK_DATE", 4.1, 4.3, 2.3, 0.9)
    box("time", "ASK_TIME", 7.4, 4.3, 2.3, 0.9)
    box("confirm", "CONFIRM", 10.5, 4.3, 2.3, 0.9)
    box("completed", "COMPLETED", 13.9, 4.3, 2.6, 0.95, fc="#eaf8ee", ec="#4a9b68", bold=True)

    box("existing_action", "ASK_EXISTING_BOOKING_ACTION", 4.6, 1.7, 3.5, 0.95)
    box("existing_pick", "ASK_EXISTING_BOOKING_PICK", 8.9, 1.7, 3.2, 0.95)
    box("max_active", "ASK_MAX_ACTIVE_BOOKINGS_ACTION", 13.0, 1.7, 3.4, 0.95)
    box("confirm_reschedule", "CONFIRM_RESCHEDULE", 16.7, 1.7, 3.0, 0.95)

    _add_arrow(ax, *port("start", "right"), *port("init", "left"))
    _add_arrow(ax, *port("init", "right"), *port("greeting", "left"), "hello / hi", fontsize=8)
    _add_arrow(ax, *port("greeting", "right"), *port("avail_date", "left"), "2 or availability", fontsize=8)
    _add_arrow(ax, *port("avail_date", "right"), *port("avail_details", "left"), "pick date", fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("avail_details", "bottom"), (15.1, 9.0), (4.9, 7.7), port("booking_for", "top")],
        "booking intent / restart / 1",
        fontsize=8,
        label_pos=(11.8, 8.8),
    )

    _add_arrow(ax, *port("init", "bottom"), *port("booking_for", "top"), "1 or booking intent", fontsize=8)
    _add_arrow(ax, *port("booking_for", "right"), *port("name", "left"), "another person", fontsize=8)
    _add_arrow(ax, *port("name", "right"), *port("phone", "left"), "valid name", fontsize=8)
    _add_arrow(ax, *port("phone", "right"), *port("clinic", "left"), "valid / same number", fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("booking_for", "bottom"), (4.1, 5.3), port("date", "top")],
        "self if known",
        fontsize=8,
        label_pos=(4.9, 5.9),
    )
    _add_elbow_arrow(
        ax,
        [port("name", "bottom"), (7.4, 5.3), (8.4, 5.3), port("time", "top")],
        "self if phone missing",
        fontsize=8,
        label_pos=(8.4, 5.9),
    )
    _add_elbow_arrow(
        ax,
        [port("clinic", "bottom"), (13.6, 5.2), (5.2, 5.2), port("date", "right")],
        "pick clinic",
        fontsize=8,
        label_pos=(9.5, 5.45),
    )
    _add_arrow(ax, *port("date", "right"), *port("time", "left"), "pick date", fontsize=8)
    _add_arrow(ax, *port("time", "right"), *port("confirm", "left"), "pick time", fontsize=8)
    _add_arrow(ax, *port("confirm", "right"), *port("completed", "left"), "confirm", fontsize=8)

    _add_elbow_arrow(
        ax,
        [port("init", "bottom"), (5.0, 8.8), (5.0, 2.5), port("existing_action", "top")],
        "existing booking",
        fontsize=8,
        label_pos=(5.2, 5.9),
    )
    _add_arrow(ax, *port("existing_action", "right"), *port("existing_pick", "left"), "multiple bookings", fontsize=8)
    _add_arrow(ax, *port("existing_pick", "right"), *port("max_active", "left"), "reschedule selected", fontsize=8)
    _add_arrow(ax, *port("max_active", "right"), *port("confirm_reschedule", "left"), "reschedule / cancel existing", fontsize=8)
    _add_elbow_arrow(
        ax,
        [port("existing_pick", "top"), (8.9, 2.8), (12.8, 3.7), port("completed", "bottom")],
        "cancel selected",
        fontsize=8,
        label_pos=(10.9, 3.0),
    )
    _add_elbow_arrow(
        ax,
        [port("existing_action", "top"), (4.6, 2.8), (12.8, 3.3), port("completed", "bottom")],
        "single reschedule",
        fontsize=8,
        label_pos=(8.7, 3.7),
    )
    _add_elbow_arrow(
        ax,
        [port("confirm_reschedule", "top"), (16.7, 2.8), (15.1, 3.6), port("completed", "right")],
        "confirm",
        fontsize=8,
        label_pos=(16.0, 3.35),
    )

    ax.text(1.0, 8.9, "Entry / availability", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 6.0, "New booking path", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 0.6, "Existing booking / reschedule path", fontsize=11, weight="bold", color="#444")
    ax.text(1.0, 0.1, "Note: back arrows, invalid-input loops, and CANCELLED/CHANGE detail are omitted here for readability.\nThe exact state coverage remains in Flowchart.md and flowchart.mmd.", fontsize=9, color="#555")

    ax.set_title("Conversation Flow (Presentation Version)", fontsize=18, weight="bold", pad=18)
    fig.tight_layout()
    fig.savefig(ROOT / "Conversation_Flow_Presentation.png", bbox_inches="tight")
    fig.savefig(ROOT / "Conversation_Flow_Presentation.svg", bbox_inches="tight")
    plt.close(fig)


def render_code_responsibility():
    fig, ax = plt.subplots(figsize=(20, 12), dpi=180)
    ax.set_xlim(0, 19)
    ax.set_ylim(0, 14)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    groups = [
        (2.7, 6.3, 4.0, 10.2, "Entry / API", "#f4fbff", "#93bdd4"),
        (7.5, 6.3, 4.3, 10.2, "Conversation Core", "#f7f4ff", "#b29ada"),
        (12.6, 6.3, 4.0, 10.2, "Runtime / Background", "#f7fff7", "#8ebd8a"),
        (17.1, 6.3, 3.2, 10.2, "Persistence / External", "#fff8f1", "#d8b184"),
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
        ax.text(x, y + h / 2 - 0.15, label, ha="center", va="center", fontsize=12, weight="bold", color="#223")

    nodes = {}

    def module_box(key, x, y, title, desc, *, fc, ec):
        nodes[key] = (x, y, 3.25, 1.45)
        _add_box(ax, x, y, 3.25, 1.45, f"{title}\n{desc}", fc=fc, ec=ec, fontsize=7.8)

    def port(key, side):
        x, y, w, h = nodes[key]
        return {
            "top": (x, y + h / 2),
            "bottom": (x, y - h / 2),
            "left": (x - w / 2, y),
            "right": (x + w / 2, y),
        }[side]

    module_box("main", 2.7, 10.4, "main.py", "startup wiring\nFastAPI routes\nscheduler + services", fc="#eef8ff", ec="#8dbbd7")
    module_box("webhooks", 2.7, 8.6, "src/api/webhooks.py", "Telegram / WhatsApp\nwebhook parsing\ndedup + queue handoff", fc="#eef8ff", ec="#8dbbd7")
    module_box("qr", 2.7, 6.3, "src/qr/checkin_service.py", "QR page submit logic\nsame-day booking\nQR overflow rules", fc="#eef8ff", ec="#8dbbd7")
    module_box("config", 2.7, 4.0, "src/config.py", "environment config\nprovider flags\nruntime settings", fc="#eef8ff", ec="#8dbbd7")

    module_box("session", 7.5, 10.4, "src/session_store.py", "load/save FSM session\nRedis first\nDB fallback", fc="#f5f0ff", ec="#a78dd8")
    module_box("fsm", 7.5, 8.6, "src/fsm/appointment_fsm.py", "state machine core\nstate dispatch\nshared helpers", fc="#f5f0ff", ec="#a78dd8")
    module_box("handlers", 7.5, 6.3, "src/fsm/handlers/*", "INIT / booking /\nexisting booking /\navailability handlers", fc="#f5f0ff", ec="#a78dd8")
    module_box("nlu", 7.5, 4.0, "src/nlu/* + src/llm/*", "intent routing\nentity extraction\nLLM fallback tasks", fc="#f5f0ff", ec="#a78dd8")

    module_box("turnq", 12.6, 10.4, "src/runtime/turn_queue.py", "worker queue\nretry / timeout\ntask processing", fc="#f3fff3", ec="#7cab78")
    module_box("kafka_runtime", 12.6, 8.6, "src/runtime/kafka_*.py", "Kafka turn bridge\nKafka notif bridge\nasync transport", fc="#f3fff3", ec="#7cab78")
    module_box("delivery", 12.6, 6.3, "src/runtime/channel_delivery.py", "send replies via\nTelegram / Twilio /\nMeta / Infobip", fc="#f3fff3", ec="#7cab78")
    module_box("background", 12.6, 4.0, "src/automation/scheduler.py\nsrc/runtime/background_workers.py", "doctor reminders\nnotification processing\noverflow + cache workers", fc="#f3fff3", ec="#7cab78")

    module_box("bookrepo", 17.1, 10.4, "src/repositories/booking_repository.py", "patient + appointment\nqueries and writes\nnotifications / reminders", fc="#fff9f0", ec="#d1a76d")
    module_box("schedrepo", 17.1, 8.6, "src/repositories/scheduling_repository.py", "clinic/date/time\navailability lookup\nRedis availability cache", fc="#fff9f0", ec="#d1a76d")
    module_box("convrepo", 17.1, 6.3, "src/repositories/conversation_repository.py", "conversation sessions\nmessage dedup table\noverflow turn queue", fc="#fff9f0", ec="#d1a76d")
    module_box("db", 17.1, 4.0, "src/db/connection.py\nsrc/db_store.py", "MySQL pool config\nrepo construction\nDB entry layer", fc="#fff9f0", ec="#d1a76d")

    _add_arrow(ax, *port("main", "right"), *port("session", "left"))
    _add_arrow(ax, *port("webhooks", "right"), *port("fsm", "left"))
    _add_arrow(ax, *port("webhooks", "right"), *port("turnq", "top"), "queue handoff", fontsize=8)
    _add_arrow(ax, *port("fsm", "bottom"), *port("handlers", "top"))
    _add_arrow(ax, *port("kafka_runtime", "bottom"), *port("delivery", "top"))
    _add_arrow(ax, *port("turnq", "right"), *port("bookrepo", "left"), "booking writes", fontsize=8)
    _add_arrow(ax, *port("kafka_runtime", "right"), *port("schedrepo", "left"), "availability sync", fontsize=8)
    _add_arrow(ax, *port("delivery", "right"), *port("convrepo", "left"), "delivery state", fontsize=8)

    _add_arrow(ax, *port("handlers", "right"), *port("bookrepo", "top"), "booking queries", fontsize=8)
    _add_arrow(ax, *port("handlers", "right"), *port("schedrepo", "top"), "availability queries", fontsize=8)
    _add_arrow(ax, *port("nlu", "right"), *port("db", "left"), "LLM / NLU support", fontsize=8)
    _add_arrow(ax, *port("session", "right"), *port("bookrepo", "top"), "session persistence", fontsize=8)
    _add_arrow(ax, *port("background", "right"), *port("db", "left"), "worker writes", fontsize=8)

    ax.text(1.1, 1.0, "Purpose: show which code area owns which responsibility. This is a code/module view, not a DB ERD or runtime sequence diagram.", fontsize=10, color="#555")
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
