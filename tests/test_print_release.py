from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen.canvas import Canvas

from builder.print_release import (
    A4_HEIGHT_PT, A4_WIDTH_PT, archive_previous_release_pdfs, booklet_page_order, impose_booklet,
)


def _numbered_pdf(path: Path, pages: int) -> None:
    canvas = Canvas(str(path), pagesize=A5)
    for number in range(1, pages + 1):
        canvas.setFont("Helvetica", 24)
        canvas.drawString(72, 72, f"SOURCE-{number}")
        canvas.showPage()
    canvas.save()


@pytest.mark.parametrize(
    ("pages", "order"),
    [
        (1, [None, 1, None, None]),
        (4, [4, 1, 2, 3]),
        (6, [None, 1, 2, None, 6, 3, 4, 5]),
        (8, [8, 1, 2, 7, 6, 3, 4, 5]),
    ],
)
def test_booklet_page_order(pages: int, order: list[int | None]) -> None:
    assert booklet_page_order(pages) == order


def test_booklet_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="at least one"):
        booklet_page_order(0)


def test_impose_booklet_creates_a4_landscape_sides_and_padding(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "booklet.pdf"
    _numbered_pdf(source, 6)

    impose_booklet(source, output)

    reader = PdfReader(output)
    assert len(reader.pages) == 4
    assert all(abs(float(page.mediabox.width) - A4_WIDTH_PT) < 0.1 for page in reader.pages)
    assert all(abs(float(page.mediabox.height) - A4_HEIGHT_PT) < 0.1 for page in reader.pages)
    assert [page.extract_text().split() for page in reader.pages] == [
        ["SOURCE-1"], ["SOURCE-2"], ["SOURCE-6", "SOURCE-3"], ["SOURCE-4", "SOURCE-5"],
    ]


def test_previous_release_pdfs_are_archived(tmp_path: Path) -> None:
    current = tmp_path / "abstract-v3.docx"
    current.touch()
    for name in ("abstract-v2.pdf", "abstract-v2-booklet.pdf", "abstract-v3.pdf", "abstract-v3-booklet.pdf"):
        (tmp_path / name).touch()

    archived = archive_previous_release_pdfs(current)

    assert {path.name for path in archived} == {"abstract-v2.pdf", "abstract-v2-booklet.pdf"}
    assert (tmp_path / "abstract-v3.pdf").is_file()
    assert (tmp_path / "abstract-v3-booklet.pdf").is_file()
