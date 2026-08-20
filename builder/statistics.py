from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from .bibliography import completed_entries, publication_counts, read_bib_entries
from .resolver import ProjectIndex


MANIFEST_NAME = "dissertation-statistics.json"


def source_statistics(
    index: ProjectIndex,
    bibliography: Path | None,
    publications_bibliography: Path | None,
    conferences_bibliography: Path | None,
) -> dict[str, int]:
    notes = [
        note for note in index.notes
        if str(note.metadata.get("type", "")).casefold() not in {"abstract", "chapter-summary"}
    ]
    publication_entries = completed_entries(read_bib_entries(publications_bibliography))
    conference_entries = completed_entries(read_bib_entries(conferences_bibliography))
    stats = {
        "chapters": sum(str(note.metadata.get("type", "")).casefold() == "chapter" for note in notes),
        "appendices": sum(str(note.metadata.get("type", "")).casefold() == "appendix" for note in notes),
        "figures": sum(len(re.findall(r"^```figure\s*$", note.body, re.MULTILINE)) for note in notes),
        "tables": sum(len(re.findall(r"^```table\s*$", note.body, re.MULTILINE)) for note in notes),
        "bibliography": len(completed_entries(read_bib_entries(bibliography))),
        "conferences": len(conference_entries),
        **publication_counts(publication_entries),
    }
    return {key: int(value) for key, value in stats.items()}


def _pages_from_pdf(path: Path) -> int:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"PDF for page counting does not exist or is empty: {path}")
    return len(PdfReader(path).pages)


def _pages_from_word(docx: Path) -> int | None:
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if os.name != "nt" or powershell is None:
        return None
    with tempfile.TemporaryDirectory(prefix="md2docx-word-pages-") as directory:
        pdf = Path(directory) / "dissertation.pdf"
        script = (
            "$path=[Environment]::GetEnvironmentVariable('MD2DOCX_STATS_PATH');"
            "$pdf=[Environment]::GetEnvironmentVariable('MD2DOCX_STATS_PDF');"
            "$word=New-Object -ComObject Word.Application;$word.Visible=$false;$word.DisplayAlerts=0;"
            "$doc=$null;try{$doc=$word.Documents.Open($path,$false,$true);"
            "$doc.ExportAsFixedFormat($pdf,17)}"
            "finally{if($doc -ne $null){$doc.Close()};$word.Quit()}"
        )
        environment = os.environ.copy()
        environment["MD2DOCX_STATS_PATH"] = str(docx.resolve())
        environment["MD2DOCX_STATS_PDF"] = str(pdf.resolve())
        try:
            subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
                env=environment,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() if exc.stderr else str(exc)
            raise RuntimeError(f"Microsoft Word failed to export the dissertation for page counting: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Microsoft Word timed out while calculating dissertation pages") from exc
        except OSError:
            return None
        return _pages_from_pdf(pdf) if pdf.is_file() else None


def _pages_from_libreoffice(docx: Path) -> int | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        return None
    with tempfile.TemporaryDirectory(prefix="md2docx-pages-") as directory:
        output = Path(directory)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(output), str(docx)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        pdf = output / f"{docx.stem}.pdf"
        return _pages_from_pdf(pdf) if pdf.is_file() else None


def measure_docx_pages(docx: Path, pdf: Path | None = None) -> int:
    if pdf is not None:
        return _pages_from_pdf(pdf)
    companion = docx.with_suffix(".pdf")
    if companion.is_file():
        return _pages_from_pdf(companion)
    pages = _pages_from_word(docx)
    if pages is None:
        pages = _pages_from_libreoffice(docx)
    if pages is None or pages < 1:
        raise RuntimeError(
            "could not obtain the final dissertation page count; install Microsoft Word or LibreOffice, "
            "or configure a dissertation PDF"
        )
    return pages


def write_statistics_manifest(
    project_root: Path,
    dissertation_docx: Path,
    statistics: dict[str, int],
) -> Path:
    path = project_root / "build" / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "dissertation_docx": dissertation_docx.resolve().relative_to(project_root.resolve()).as_posix(),
        "statistics": statistics,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_statistics_manifest(project_root: Path) -> tuple[Path, dict[str, int]]:
    path = project_root / "build" / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != 1:
            raise ValueError("unsupported statistics manifest schema")
        source = project_root / str(payload["dissertation_docx"])
        stats = payload["statistics"]
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("dissertation statistics manifest is missing or invalid; build the dissertation first") from exc
    if not source.is_file():
        raise ValueError(f"dissertation referenced by statistics manifest does not exist: {source}")
    if not isinstance(stats, dict) or not all(isinstance(key, str) and isinstance(value, int) for key, value in stats.items()):
        raise ValueError("dissertation statistics manifest contains invalid counters")
    return source, stats
