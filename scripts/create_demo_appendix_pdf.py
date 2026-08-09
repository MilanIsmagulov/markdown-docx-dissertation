from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "content" / "assets" / "appendices" / "demo-protocol.pdf"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    font_path = Path(r"C:\Windows\Fonts\times.ttf")
    font_name = "TimesNewRoman"
    pdfmetrics.registerFont(TTFont(font_name, font_path))
    document = canvas.Canvas(str(OUTPUT), pagesize=A4)
    width, height = A4

    for page in (1, 2):
        document.setFont(font_name, 14)
        document.drawCentredString(width / 2, height - 70, "ДЕМОНСТРАЦИОННЫЙ ПРОТОКОЛ")
        document.setFont(font_name, 12)
        document.drawString(70, height - 120, f"Страница {page} из 2")
        document.drawString(70, height - 155, "Этот PDF используется для проверки встраивания приложений.")
        document.drawString(70, height - 185, "В реальном проекте замените его актом, протоколом или свидетельством.")
        document.rect(70, height - 360, width - 140, 130)
        document.drawString(85, height - 270, "Область демонстрационных данных")
        document.drawString(85, height - 305, f"Номер листа: {page}")
        document.showPage()

    document.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
