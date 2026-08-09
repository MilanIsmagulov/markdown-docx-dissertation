from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "content" / "assets" / "images" / "multimodal-pipeline.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1400, 360), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/times.ttf")
    font = ImageFont.truetype(str(font_path), 34) if font_path.exists() else ImageFont.load_default()
    boxes = [
        (35, 110, 285, 250, "Видео, аудио,\nслайды"),
        (390, 110, 640, 250, "Извлечение\nпризнаков"),
        (745, 110, 995, 250, "Слияние\nмодальностей"),
        (1110, 110, 1365, 250, "Документ"),
    ]
    for left, top, right, bottom, label in boxes:
        draw.rounded_rectangle((left, top, right, bottom), radius=10, fill="#f2f2f2", outline="#222222", width=3)
        draw.multiline_text(((left + right) / 2, (top + bottom) / 2), label, font=font, fill="#111111", anchor="mm", align="center")
    for start, end in ((285, 390), (640, 745), (995, 1110)):
        draw.line((start, 180, end - 14, 180), fill="#222222", width=4)
        draw.polygon(((end - 14, 170), (end, 180), (end - 14, 190)), fill="#222222")
    image.save(output, format="PNG", dpi=(300, 300))


if __name__ == "__main__":
    main()
