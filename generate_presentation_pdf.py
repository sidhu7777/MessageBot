from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "System_Diagrams_Presentation.pdf"

DIAGRAMS = [
    ("Frontend ER Diagram", ROOT / "Frontend_er_diagam_app.png"),
    ("Backend ER Diagram", ROOT / "ER_diagram_Backend_diagram_app.png"),
    ("Database ER Diagram", ROOT / "Database_ER_diagram.png"),
    ("Current Model Architecture", ROOT / "Current_Model_Architecture.png"),
    ("Code / Module Responsibility Map", ROOT / "Code_Module_Responsibility.png"),
    ("Current FSM Flow", ROOT / "Current_FSM_Flow.png"),
]


def choose_page_size(image_path: Path) -> tuple[float, float]:
    with Image.open(image_path) as img:
        width, height = img.size
    return portrait(A4) if height > width else landscape(A4)


def portrait(page_size: tuple[float, float]) -> tuple[float, float]:
    width, height = page_size
    return (min(width, height), max(width, height))


def fit_image(img_width: int, img_height: int, box_width: float, box_height: float) -> tuple[float, float]:
    scale = min(box_width / img_width, box_height / img_height)
    return img_width * scale, img_height * scale


def build_pdf() -> Path:
    first_page = choose_page_size(DIAGRAMS[0][1])
    pdf = canvas.Canvas(str(OUTPUT), pagesize=first_page)

    for index, (title, image_path) in enumerate(DIAGRAMS):
        page_size = choose_page_size(image_path)
        if index > 0:
            pdf.setPageSize(page_size)

        page_width, page_height = page_size
        left_margin = 28
        right_margin = 28
        bottom_margin = 24
        title_top = 24
        title_gap = 18

        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawCentredString(page_width / 2, page_height - title_top, title)

        with Image.open(image_path) as img:
            img_width, img_height = img.size
            draw_width, draw_height = fit_image(
                img_width,
                img_height,
                page_width - left_margin - right_margin,
                page_height - bottom_margin - title_top - title_gap - 24,
            )

            x = (page_width - draw_width) / 2
            y = bottom_margin
            pdf.drawImage(
                ImageReader(img),
                x,
                y,
                width=draw_width,
                height=draw_height,
                preserveAspectRatio=True,
                mask="auto",
            )

        pdf.showPage()

    pdf.save()
    return OUTPUT


if __name__ == "__main__":
    output = build_pdf()
    print(f"Generated {output.name}")
