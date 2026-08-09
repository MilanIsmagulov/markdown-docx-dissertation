from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


DIRECTIVE_RE = re.compile(r"^```(?P<kind>table|figure|equation)\s*$")
REFERENCE_RE = re.compile(r"\{\{ref:(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")
NUMBER_RE = re.compile(r"\{\{number:(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")


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


def process_assets(markdown: str, content_dir: Path) -> str:
    lines = markdown.splitlines()
    counters = {"table": 0, "figure": 0, "equation": 0}
    labels: dict[tuple[str, str], ObjectNumber] = {}
    chapter = 0
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.startswith("# "):
            chapter += 1
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
