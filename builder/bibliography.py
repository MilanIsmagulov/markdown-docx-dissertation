from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENTRY_START_RE = re.compile(r"@(?P<type>[A-Za-z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,", re.MULTILINE)
FIELD_RE = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?:\{(?P<braced>.*?)\}|\"(?P<quoted>.*?)\"|(?P<bare>[^,\r\n]+))\s*,?",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class BibEntry:
    entry_type: str
    key: str
    fields: dict[str, str]

    @property
    def keywords(self) -> set[str]:
        raw = self.fields.get("keywords", "")
        return {item.strip().casefold() for item in re.split(r"[,;]", raw) if item.strip()}

    @property
    def is_pending(self) -> bool:
        return self.fields.get("status", "").casefold() == "pending" or "pending" in self.keywords


def read_bib_entries(path: Path | None) -> list[BibEntry]:
    if path is None or not path.is_file():
        return []
    text = "\n".join(
        line for line in path.read_text(encoding="utf-8-sig").splitlines() if not line.lstrip().startswith("%")
    )
    entries: list[BibEntry] = []
    for match in ENTRY_START_RE.finditer(text):
        cursor = match.end()
        depth = 1
        quoted = False
        escaped = False
        while cursor < len(text) and depth:
            char = text[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted and char == "{":
                depth += 1
            elif not quoted and char == "}":
                depth -= 1
            cursor += 1
        body = text[match.end() : cursor - 1]
        fields = {}
        for field in FIELD_RE.finditer(body):
            value = field["braced"] if field["braced"] is not None else field["quoted"]
            if value is None:
                value = field["bare"] or ""
            fields[field["name"].casefold()] = " ".join(value.strip().split())
        entries.append(BibEntry(match["type"].casefold(), match["key"], fields))
    return entries


def completed_entries(entries: list[BibEntry]) -> list[BibEntry]:
    return [entry for entry in entries if not entry.is_pending]


def publication_counts(entries: list[BibEntry]) -> dict[str, int]:
    entries = completed_entries(entries)
    return {
        "publications": len(entries),
        "publications_vak": sum("vak" in entry.keywords for entry in entries),
        "publications_scopus_wos": sum(bool(entry.keywords & {"scopus", "wos", "web-of-science"}) for entry in entries),
        "publications_scopus": sum("scopus" in entry.keywords for entry in entries),
        "publications_wos": sum(bool(entry.keywords & {"wos", "web-of-science"}) for entry in entries),
        "publications_other": sum("other" in entry.keywords for entry in entries),
        "patents": sum(entry.entry_type == "patent" or "patent" in entry.keywords for entry in entries),
        "software_registrations": sum(
            entry.entry_type == "software" or bool(entry.keywords & {"software", "software-registration"})
            for entry in entries
        ),
    }


def format_publication_authors(raw: str) -> str:
    """Render BibTeX authors as ``Surname N. N.`` without reordering them."""
    rendered: list[str] = []
    for value in re.split(r"\s+and\s+", raw.strip(), flags=re.IGNORECASE):
        value = " ".join(value.split())
        if not value:
            continue
        if "," in value:
            surname, given = (part.strip() for part in value.split(",", 1))
        else:
            parts = value.split()
            surname, given = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (value, "")
        initials = []
        for name in given.split():
            letters = [part[0] for part in name.replace(".", "").split("-") if part]
            if letters:
                initials.append("-".join(f"{letter}." for letter in letters))
        rendered.append(" ".join(part for part in (surname, " ".join(initials)) if part))
    return "; ".join(rendered)


def format_publication(entry: BibEntry) -> str:
    fields = entry.fields
    authors = format_publication_authors(fields.get("author", ""))
    title = fields.get("title", entry.key)
    parts = [f"{authors}. {title}" if authors else title]
    if entry.entry_type == "article":
        parts.append(fields.get("journal") or fields.get("eprint", ""))
    elif entry.entry_type in {"inproceedings", "conference"}:
        parts.append(fields.get("booktitle", ""))
    elif entry.entry_type in {"online", "www", "electronic"}:
        parts[0] += " [Электронный ресурс]"
    elif entry.entry_type == "patent" and fields.get("number"):
        parts.append(f"Патент {fields['number']}")
    elif entry.entry_type == "software" and fields.get("number"):
        parts.append(f"Свидетельство о государственной регистрации программы для ЭВМ {fields['number']}")
    parts.append(fields.get("year", ""))
    if fields.get("volume"):
        parts.append(f"Т. {fields['volume']}")
    if fields.get("number") and entry.entry_type not in {"patent", "software"}:
        parts.append(f"№ {fields['number']}")
    if fields.get("pages"):
        parts.append(f"С. {fields['pages'].replace('--', '–')}")
    if fields.get("doi"):
        parts.append(f"DOI: {fields['doi']}")
    if fields.get("url"):
        parts.append(f"URL: {fields['url']}")
    if fields.get("urldate"):
        parts.append(f"дата обращения: {fields['urldate']}")
    return ". ".join(item.rstrip(".") for item in parts if item) + "."


DEFAULT_PUBLICATION_GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "vak", "title": "Публикации в изданиях, рекомендованных ВАК РФ", "keywords_any": ["vak"]},
    {"id": "indexed", "title": "Публикации в изданиях, индексируемых в Scopus и Web of Science", "keywords_any": ["scopus", "wos", "web-of-science"]},
    {"id": "rights", "title": "Свидетельства и патенты", "types": ["software", "patent"]},
    {"id": "other", "title": "Публикации в других изданиях", "keywords_any": ["other"], "unmatched": True},
)


def group_publications(entries: list[BibEntry], config: dict | None = None) -> list[tuple[str, list[BibEntry]]]:
    raw_groups = (config or {}).get("groups", DEFAULT_PUBLICATION_GROUPS)
    groups = raw_groups if isinstance(raw_groups, (list, tuple)) else DEFAULT_PUBLICATION_GROUPS
    remaining = list(entries)
    result: list[tuple[str, list[BibEntry]]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        types = {str(value).casefold() for value in group.get("types", [])}
        keywords = {str(value).casefold() for value in group.get("keywords_any", [])}
        unmatched = bool(group.get("unmatched", False))
        selected = [
            entry for entry in remaining
            if unmatched or (types and entry.entry_type in types) or (keywords and bool(entry.keywords & keywords))
        ]
        if selected:
            result.append((str(group.get("title", group.get("id", "Публикации"))), selected))
            selected_ids = {id(entry) for entry in selected}
            remaining = [entry for entry in remaining if id(entry) not in selected_ids]
    return result
