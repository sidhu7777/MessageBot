import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.channel_delivery import ChannelDelivery


def test_telegram_formatter_bolds_patient_id_labels_only():
    body = (
        "Appointment booked successfully.\n"
        "*Patient ID:* 5\n"
        "Clinic: Aditya"
    )

    formatted = ChannelDelivery._format_telegram_text(body)

    assert "<b>Patient ID:</b> 5" in formatted
    assert "*Patient ID:*" not in formatted
    assert "Clinic: Aditya" in formatted


def test_telegram_formatter_bolds_hindi_patient_id_label():
    body = (
        "अपॉइंटमेंट सफलतापूर्वक बुक हो गई।\n"
        "*रोगी आईडी:* 5\n"
        "क्लिनिक: आदित्य"
    )

    formatted = ChannelDelivery._format_telegram_text(body)

    assert "<b>रोगी आईडी:</b> 5" in formatted
    assert "*रोगी आईडी:*" not in formatted
    assert "क्लिनिक: आदित्य" in formatted
