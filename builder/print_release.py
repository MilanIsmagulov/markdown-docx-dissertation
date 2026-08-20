from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation


A4_WIDTH_PT = 841.8898
A4_HEIGHT_PT = 595.2756


def booklet_page_order(page_count: int) -> list[int | None]:
    """Return left/right page order for A4 landscape booklet sides."""
    if page_count < 1:
        raise ValueError("a booklet requires at least one source page")
    padded = ((page_count + 3) // 4) * 4
    order: list[int | None] = []
    for sheet in range(padded // 4):
        candidates = (
            padded - 2 * sheet,
            1 + 2 * sheet,
            2 + 2 * sheet,
            padded - 1 - 2 * sheet,
        )
        order.extend(page if page <= page_count else None for page in candidates)
    return order


def impose_booklet(source_pdf: Path, output_pdf: Path) -> Path:
    reader = PdfReader(source_pdf)
    order = booklet_page_order(len(reader.pages))
    writer = PdfWriter()
    slot_width = A4_WIDTH_PT / 2
    for side in range(0, len(order), 2):
        sheet = writer.add_blank_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
        for slot, page_number in enumerate(order[side : side + 2]):
            if page_number is None:
                continue
            source = reader.pages[page_number - 1]
            source_width = float(source.mediabox.width)
            source_height = float(source.mediabox.height)
            scale = min(slot_width / source_width, A4_HEIGHT_PT / source_height)
            x = slot * slot_width + (slot_width - source_width * scale) / 2
            y = (A4_HEIGHT_PT - source_height * scale) / 2
            sheet.merge_transformed_page(source, Transformation().scale(scale).translate(x, y))
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as stream:
        writer.write(stream)
    return output_pdf


def _export_with_word(docx: Path, output_pdf: Path) -> bool:
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if os.name != "nt" or powershell is None:
        return False
    script = (
        "$path=[Environment]::GetEnvironmentVariable('MD2DOCX_RELEASE_DOCX');"
        "$pdf=[Environment]::GetEnvironmentVariable('MD2DOCX_RELEASE_PDF');"
        "$word=New-Object -ComObject Word.Application;$word.Visible=$false;$word.DisplayAlerts=0;"
        "$doc=$null;try{$doc=$word.Documents.Open($path,$false,$true);"
        "$doc.Fields.Update()|Out-Null;$doc.ExportAsFixedFormat($pdf,17)}"
        "finally{if($doc -ne $null){$doc.Close()};$word.Quit()}"
    )
    environment = os.environ.copy()
    environment["MD2DOCX_RELEASE_DOCX"] = str(docx.resolve())
    environment["MD2DOCX_RELEASE_PDF"] = str(output_pdf.resolve())
    try:
        subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, text=True, timeout=180, env=environment,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return output_pdf.is_file() and output_pdf.stat().st_size > 0


def _export_with_libreoffice(docx: Path, output_pdf: Path) -> bool:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        return False
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(output_pdf.parent), str(docx)],
            check=True, capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    generated = output_pdf.parent / f"{docx.stem}.pdf"
    if generated != output_pdf and generated.is_file():
        generated.replace(output_pdf)
    return output_pdf.is_file() and output_pdf.stat().st_size > 0


def export_docx_pdf(docx: Path, output_pdf: Path) -> Path:
    if not docx.is_file():
        raise ValueError(f"DOCX does not exist: {docx}")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    if _export_with_word(docx, output_pdf) or _export_with_libreoffice(docx, output_pdf):
        reader = PdfReader(output_pdf)
        if not reader.pages:
            raise RuntimeError(f"exported PDF is empty: {output_pdf}")
        return output_pdf
    raise RuntimeError("could not export DOCX to PDF; install Microsoft Word or LibreOffice")


def build_print_release(docx: Path) -> tuple[Path, Path]:
    pdf = docx.with_suffix(".pdf")
    booklet = docx.with_name(f"{docx.stem}-booklet.pdf")
    export_docx_pdf(docx, pdf)
    impose_booklet(pdf, booklet)
    return pdf, booklet


def archive_previous_release_pdfs(current_docx: Path) -> list[Path]:
    base = current_docx.stem.rsplit("-v", 1)[0]
    current_names = {f"{current_docx.stem}.pdf", f"{current_docx.stem}-booklet.pdf"}
    archive = current_docx.parent / ".old"
    archived: list[Path] = []
    for candidate in sorted(current_docx.parent.glob(f"{base}-v*.pdf")):
        if candidate.name in current_names:
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / candidate.name
        candidate.replace(destination)
        archived.append(destination)
    return archived
