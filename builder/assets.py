from __future__ import annotations

import csv
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml


DIRECTIVE_RE = re.compile(r"^```(?P<kind>table|figure|equation)\s*$")
REFERENCE_RE = re.compile(r"\{\{ref:(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")
NUMBER_RE = re.compile(r"\{\{number:(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")
CHAPTER_RE = re.compile(r"^#\s+Глава\s+(?P<number>\d+)\b", re.IGNORECASE)
PDF_EMBED_RE = re.compile(
    r"^\s*!\[\[(?P<target>[^]|#]+\.pdf)(?:#page=(?P<page>\d+))?(?:\|[^]]+)?]]\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ObjectNumber:
    kind: str
    number: str


def _read_rows(path: Path, sheet: str | None) -> list[list[str]]:
    if path.suffix.casefold() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            return [[cell.strip() for cell in row] for row in csv.reader(source, delimiter=delimiter)]
    if path.suffix.casefold() == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("XLSX import requires the 'openpyxl' package") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook[sheet] if sheet else workbook.active
        rows = [["" if value is None else str(value) for value in row] for row in worksheet.iter_rows(values_only=True)]
        workbook.close()
        return rows
    raise ValueError(f"unsupported table asset: {path.suffix}")


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        raise ValueError("table asset contains no rows")
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(_escape_cell(cell) for cell in normalized[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in normalized[1:]]
    return "\n".join((header, separator, *body))


def _load_directive(lines: list[str], start: int) -> tuple[dict, int]:
    closing = next((index for index in range(start + 1, len(lines)) if lines[index].strip() == "```"), None)
    if closing is None:
        raise ValueError(f"asset directive at line {start + 1} is not closed")
    config = yaml.safe_load("\n".join(lines[start + 1 : closing])) or {}
    if not isinstance(config, dict):
        raise ValueError(f"asset directive at line {start + 1} must contain a YAML mapping")
    return config, closing + 1


def _render_pdf_pages(source: Path, content_dir: Path, page: int | None = None) -> list[Path]:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise ValueError("PDF embedding requires Poppler's pdftoppm executable")
    executable = Path(pdftoppm)
    if executable.suffix.casefold() in {".cmd", ".bat"}:
        runtime_root = executable.parents[2]
        native_candidates = list(runtime_root.glob("native/poppler/**/pdftoppm.exe"))
        if native_candidates:
            pdftoppm = str(native_candidates[0])
    cache = content_dir.parent / "build" / "pdf-pages" / source.stem
    cache.mkdir(parents=True, exist_ok=True)
    prefix = cache / "page"
    for stale_page in cache.glob("page-*.png"):
        stale_page.unlink()
    command = [pdftoppm, "-png", "-r", "200"]
    if page is not None:
        command.extend(["-f", str(page), "-l", str(page)])
    command.extend([str(source), str(prefix)])
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ValueError(f"failed to render PDF {source}: {detail}") from exc
    pages = sorted(cache.glob("page-*.png"), key=lambda path: int(path.stem.rsplit("-", 1)[1]))
    if page is not None:
        pages = [path for path in pages if int(path.stem.rsplit("-", 1)[1]) == page]
    if not pages:
        raise ValueError(f"PDF produced no pages: {source}")
    return pages


def process_assets(markdown: str, content_dir: Path) -> str:
    lines = markdown.splitlines()
    counters = {"table": 0, "figure": 0, "equation": 0}
    labels: dict[tuple[str, str], ObjectNumber] = {}
    chapter = 0
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        pdf_match = PDF_EMBED_RE.match(line)
        if pdf_match is not None:
            source = (content_dir / pdf_match["target"].strip()).resolve()
            if not source.is_relative_to(content_dir.resolve()) or not source.is_file():
                raise ValueError(f"PDF asset not found: {source}")
            requested_page = int(pdf_match["page"]) if pdf_match["page"] else None
            pages = _render_pdf_pages(source, content_dir, requested_page)
            for page_index, page_path in enumerate(pages):
                if page_index:
                    output.extend(("[[PDF_PAGE_BREAK]]", ""))
                output.extend((f"![](<{page_path.as_posix()}>){{width=160mm}}", ""))
            index += 1
            continue
        chapter_match = CHAPTER_RE.match(line)
        if chapter_match is not None:
            chapter = int(chapter_match["number"])
            counters = {kind: 0 for kind in counters}
            output.append(line)
            index += 1
            continue
        match = DIRECTIVE_RE.match(line)
        if match is None:
            output.append(line)
            index += 1
            continue

        kind = match["kind"]
        config, index = _load_directive(lines, index)
        object_id = str(config.get("id", "")).strip()
        caption = str(config.get("caption", "")).strip()
        if not object_id:
            raise ValueError(f"{kind} directive requires an id")
        if (kind, object_id) in labels:
            raise ValueError(f"duplicate {kind} id: {object_id}")
        counters[kind] += 1
        number = f"{chapter}.{counters[kind]}" if chapter else str(counters[kind])
        labels[(kind, object_id)] = ObjectNumber(kind, number)

        if kind == "table":
            source = content_dir / str(config.get("source", ""))
            if not source.is_file():
                raise ValueError(f"table asset not found: {source}")
            rows = _read_rows(source, str(config["sheet"]) if config.get("sheet") else None)
            output.extend((f"Таблица {number} – {caption}", "", _markdown_table(rows), ""))
        elif kind == "figure":
            source = content_dir / str(config.get("source", ""))
            if not source.is_file():
                raise ValueError(f"figure asset not found: {source}")
            width = str(config.get("width", "140mm"))
            output.extend((f"![Рисунок {number} – {caption}](<{source.as_posix()}>){{width={width}}}", ""))
        else:
            latex = str(config.get("latex", "")).strip()
            if not latex:
                raise ValueError("equation directive requires latex")
            output.extend((f"[[EQUATION:{number}]]", "", "$$", latex, "$$", ""))

    assembled = "\n".join(output)

    def replace_reference(match: re.Match[str]) -> str:
        key = (match["kind"], match["id"])
        if key not in labels:
            raise ValueError(f"unknown {match['kind']} reference: {match['id']}")
        number = labels[key].number
        return {
            "table": f"таблица {number}",
            "figure": f"рисунок {number}",
            "equation": f"({number})",
        }[match["kind"]]

    assembled = REFERENCE_RE.sub(replace_reference, assembled)

    def replace_number(match: re.Match[str]) -> str:
        key = (match["kind"], match["id"])
        if key not in labels:
            raise ValueError(f"unknown {match['kind']} reference: {match['id']}")
        return labels[key].number

    return NUMBER_RE.sub(replace_number, assembled).strip() + "\n"
