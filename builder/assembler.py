from __future__ import annotations

import re
from pathlib import Path

from .model import Note
from .parser import HEADING_RE, INLINE_CODE_RE, WIKI_LINK_RE
from .resolver import ProjectIndex


def _section(note: Note, heading: str | None) -> str:
    if heading is None:
        return note.body.strip()
    wanted = " ".join(heading.casefold().split())
    lines = note.body.splitlines()
    start: int | None = None
    level = 7
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match and " ".join(match["text"].casefold().split()) == wanted:
            start = index + 1
            level = len(match["marks"])
            break
    if start is None:
        raise ValueError(f"heading '{heading}' not found in {note.path}")
    end = len(lines)
    for index in range(start, len(lines)):
        match = HEADING_RE.match(lines[index])
        if match and len(match["marks"]) <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def assemble_note(
    index: ProjectIndex,
    note: Note,
    heading: str | None = None,
    stack: tuple[Path, ...] = (),
    source_overrides: dict[str, str] | None = None,
) -> str:
    if note.path in stack:
        raise ValueError(f"transclusion cycle reaches {note.path}")
    source = _section(note, heading)
    output: list[str] = []
    in_fence = False
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        searchable = INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)
        cursor = 0
        expanded: list[str] = []
        for match in WIKI_LINK_RE.finditer(searchable):
            if not match["embed"]:
                continue
            expanded.append(line[cursor : match.start()])
            if match["target"].strip().casefold().endswith(".pdf"):
                expanded.append(line[match.start() : match.end()])
                cursor = match.end()
                continue
            link = next(item for item in note.links if item.raw == line[match.start() : match.end()])
            target, problem = index.resolve(link)
            if problem is not None or target is None:
                raise ValueError(problem.message if problem else f"unresolved transclusion {link.raw}")
            override = (source_overrides or {}).get((target.note_id or "").casefold())
            if override:
                target = index.by_id.get(override.casefold())
                if target is None:
                    raise ValueError(f"configured research source '{override}' does not exist")
            expanded.append(assemble_note(index, target, link.heading, (*stack, note.path), source_overrides))
            cursor = match.end()
        if expanded:
            expanded.append(line[cursor:])
            output.append("".join(expanded))
        else:
            output.append(line)
    return "\n".join(output).strip() + "\n"
