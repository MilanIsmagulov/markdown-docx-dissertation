from __future__ import annotations

from pathlib import Path

from .model import Diagnostic, Note
from .resolver import ProjectIndex


def _heading_key(text: str) -> str:
    return " ".join(text.casefold().split())


def validate_index(index: ProjectIndex, root_note: Path | None = None) -> list[Diagnostic]:
    diagnostics = list(index.diagnostics)
    if root_note is not None and not root_note.is_file():
        diagnostics.append(Diagnostic("E_ROOT_MISSING", f"entry document does not exist: {root_note}"))

    for note in index.notes:
        seen_headings: set[str] = set()
        for heading in note.headings:
            key = _heading_key(heading.text)
            if key in seen_headings:
                diagnostics.append(
                    Diagnostic("W_HEADING_DUPLICATE", f"duplicate heading '{heading.text}'", heading.location, "warning")
                )
            seen_headings.add(key)
        for link in note.links:
            target, problem = index.resolve(link)
            if problem:
                diagnostics.append(problem)
                continue
            if link.heading and target is not None:
                target_headings = {_heading_key(item.text) for item in target.headings}
                if _heading_key(link.heading) not in target_headings:
                    diagnostics.append(
                        Diagnostic(
                            "E_HEADING_MISSING",
                            f"heading '{link.heading}' does not exist in '{link.target}'",
                            link.location,
                        )
                    )
    diagnostics.extend(_validate_transclusion_cycles(index))
    return diagnostics


def _validate_transclusion_cycles(index: ProjectIndex) -> list[Diagnostic]:
    edges: dict[Path, list[tuple[Note, object]]] = {note.path: [] for note in index.notes}
    for note in index.notes:
        for link in note.links:
            if link.kind != "transclusion":
                continue
            target, problem = index.resolve(link)
            if target is not None and problem is None:
                edges[note.path].append((target, link))

    diagnostics: list[Diagnostic] = []
    visiting: set[Path] = set()
    visited: set[Path] = set()

    def visit(note: Note, trail: list[str]) -> None:
        if note.path in visited:
            return
        visiting.add(note.path)
        for target, link in edges[note.path]:
            if target.path in visiting:
                cycle = " -> ".join([*trail, note.title, target.title])
                diagnostics.append(Diagnostic("E_TRANSCLUSION_CYCLE", f"transclusion cycle: {cycle}", link.location))
            else:
                visit(target, [*trail, note.title])
        visiting.remove(note.path)
        visited.add(note.path)

    for note in index.notes:
        if note.path not in visited:
            visit(note, [])
    return diagnostics

