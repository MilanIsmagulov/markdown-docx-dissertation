from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


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
