from __future__ import annotations

import re
from pathlib import Path

import yaml

from .model import Diagnostic, Note, SourceLocation
from .resolver import ProjectIndex


DIRECTIVE_BLOCK_RE = re.compile(r"^```(?P<kind>table|figure|equation)\s*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL)
OBJECT_REFERENCE_RE = re.compile(r"\{\{(?:ref|number):(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")
CITATION_RE = re.compile(r"(?<![\w@])@(?P<key>[A-Za-z0-9_:.+/-]+)")
BIB_KEY_RE = re.compile(r"@[A-Za-z]+\s*\{\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE)


def _heading_key(text: str) -> str:
    return " ".join(text.casefold().split())


def validate_index(
    index: ProjectIndex,
    root_note: Path | None = None,
    bibliography: Path | None = None,
) -> list[Diagnostic]:
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
            if link.kind == "transclusion" and link.target.casefold().endswith(".pdf"):
                asset = (index.content_dir / link.target).resolve()
                if not asset.is_relative_to(index.content_dir.resolve()) or not asset.is_file():
                    diagnostics.append(Diagnostic("E_ASSET_MISSING", f"PDF asset does not exist: '{link.target}'", link.location))
                continue
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
    diagnostics.extend(_validate_document_sources(index, bibliography))
    diagnostics.extend(_validate_reachability(index, root_note))
    return diagnostics


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _validate_document_sources(index: ProjectIndex, bibliography: Path | None) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    objects: dict[tuple[str, str], SourceLocation] = {}
    references: list[tuple[str, str, SourceLocation]] = []
    citations: dict[str, SourceLocation] = {}
    allowed_extensions = {"table": {".csv", ".tsv", ".xlsx"}, "figure": {".png", ".jpg", ".jpeg", ".svg"}}

    for note in index.notes:
        for match in DIRECTIVE_BLOCK_RE.finditer(note.body):
            location = SourceLocation(note.path, _line_for_offset(note.body, match.start()))
            try:
                config = yaml.safe_load(match["body"]) or {}
            except yaml.YAMLError as exc:
                diagnostics.append(Diagnostic("E_DIRECTIVE_YAML", f"invalid {match['kind']} directive: {exc}", location))
                continue
            if not isinstance(config, dict):
                diagnostics.append(Diagnostic("E_DIRECTIVE_CONFIG", f"{match['kind']} directive must contain a mapping", location))
                continue
            kind = match["kind"]
            object_id = str(config.get("id", "")).strip()
            if not object_id:
                diagnostics.append(Diagnostic("E_OBJECT_ID_MISSING", f"{kind} directive requires id", location))
            else:
                key = (kind, object_id)
                if key in objects:
                    diagnostics.append(Diagnostic("E_OBJECT_ID_DUPLICATE", f"duplicate {kind} id '{object_id}'", location))
                else:
                    objects[key] = location
            if kind in allowed_extensions:
                caption = str(config.get("caption", "")).strip()
                if not caption:
                    diagnostics.append(Diagnostic("E_CAPTION_MISSING", f"{kind} '{object_id}' requires caption", location))
                source_value = str(config.get("source", "")).strip()
                source = (index.content_dir / source_value).resolve()
                if not source_value or not source.is_relative_to(index.content_dir.resolve()) or not source.is_file():
                    diagnostics.append(Diagnostic("E_ASSET_MISSING", f"{kind} asset does not exist: '{source_value}'", location))
                elif source.suffix.casefold() not in allowed_extensions[kind]:
                    diagnostics.append(Diagnostic("E_ASSET_TYPE", f"unsupported {kind} asset type: '{source.suffix}'", location))
            elif not str(config.get("latex", "")).strip():
                diagnostics.append(Diagnostic("E_EQUATION_EMPTY", f"equation '{object_id}' requires latex", location))

        for match in OBJECT_REFERENCE_RE.finditer(note.body):
            references.append((match["kind"], match["id"], SourceLocation(note.path, _line_for_offset(note.body, match.start()))))
        for match in CITATION_RE.finditer(note.body):
            citations.setdefault(match["key"], SourceLocation(note.path, _line_for_offset(note.body, match.start())))

    for kind, object_id, location in references:
        if (kind, object_id) not in objects:
            diagnostics.append(Diagnostic("E_OBJECT_REFERENCE_MISSING", f"unknown {kind} reference '{object_id}'", location))

    if bibliography is not None and bibliography.is_file():
        bib_keys = {match["key"] for match in BIB_KEY_RE.finditer(bibliography.read_text(encoding="utf-8-sig"))}
        for key, location in citations.items():
            if key not in bib_keys:
                diagnostics.append(Diagnostic("E_CITATION_MISSING", f"citation key '{key}' is absent from bibliography", location))
        for key in sorted(bib_keys - citations.keys()):
            diagnostics.append(Diagnostic("W_BIB_UNUSED", f"bibliography entry '{key}' is not cited", severity="warning"))
    return diagnostics


def _validate_reachability(index: ProjectIndex, root_note: Path | None) -> list[Diagnostic]:
    if root_note is None or not root_note.is_file():
        return []
    root = next((note for note in index.notes if note.path.resolve() == root_note.resolve()), None)
    if root is None:
        return []
    reachable: set[Path] = set()

    def visit(note: Note) -> None:
        if note.path in reachable:
            return
        reachable.add(note.path)
        for link in note.links:
            if link.kind != "transclusion" or link.target.casefold().endswith(".pdf"):
                continue
            target, problem = index.resolve(link)
            if target is not None and problem is None:
                visit(target)

    visit(root)
    return [
        Diagnostic("W_NOTE_ORPHAN", "note is not included from the root document", SourceLocation(note.path, 1), "warning")
        for note in index.notes
        if note.path not in reachable and not note.path.name.startswith("_")
    ]


def _validate_transclusion_cycles(index: ProjectIndex) -> list[Diagnostic]:
    edges: dict[Path, list[tuple[Note, object]]] = {note.path: [] for note in index.notes}
    for note in index.notes:
        for link in note.links:
            if link.kind != "transclusion":
                continue
            if link.target.casefold().endswith(".pdf"):
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
