from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .model import Diagnostic, Link, Note
from .parser import ParseError, parse_note


def _key(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if value.casefold().endswith(".md"):
        value = value[:-3]
    return value.casefold()


@dataclass(slots=True)
class ProjectIndex:
    root: Path
    content_dir: Path
    notes: list[Note] = field(default_factory=list)
    by_id: dict[str, Note] = field(default_factory=dict)
    by_name: dict[str, list[Note]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def resolve(self, link: Link) -> tuple[Note | None, Diagnostic | None]:
        id_match = self.by_id.get(_key(link.target))
        if id_match is not None:
            return id_match, None
        matches = self.by_name.get(_key(link.target), [])
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, Diagnostic("E_LINK_MISSING", f"unresolved target '{link.target}'", link.location)
        paths = ", ".join(str(note.path.relative_to(self.root)) for note in matches)
        return None, Diagnostic("E_LINK_AMBIGUOUS", f"target '{link.target}' matches: {paths}", link.location)


def build_index(project_root: Path, content_dir: Path) -> ProjectIndex:
    index = ProjectIndex(project_root, content_dir)
    for path in sorted(content_dir.rglob("*.md")):
        try:
            note = parse_note(path)
        except ParseError as exc:
            index.diagnostics.append(Diagnostic("E_PARSE", str(exc)))
            continue
        index.notes.append(note)
        for name_key in {_key(path.stem), _key(path.relative_to(content_dir).as_posix())}:
            index.by_name.setdefault(name_key, []).append(note)
        if note.note_id:
            key = _key(note.note_id)
            if key in index.by_id:
                other = index.by_id[key]
                index.diagnostics.append(
                    Diagnostic("E_ID_DUPLICATE", f"ID '{note.note_id}' is also declared by {other.path.relative_to(project_root)}")
                )
            else:
                index.by_id[key] = note
    return index
