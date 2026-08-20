from __future__ import annotations

import re
from pathlib import Path

from .model import Diagnostic, Note, SourceLocation
from .resolver import ProjectIndex


DEFAULT_PLACEHOLDER_MARKERS = ("[указать", "_____", "________", "20__")
KNOWN_METADATA = {"id", "type", "title", "source", "chapter", "paragraph", "number"}
CONTAINER_TYPES = {"document", "chapter", "structural-section", "appendices", "bibliography"}
CHAPTER_ID_RE = re.compile(r"^chapter:(?P<number>\d+)$", re.IGNORECASE)
PARAGRAPH_ID_RE = re.compile(r"^paragraph:(?P<chapter>\d+)\.(?P<number>\d+)$", re.IGNORECASE)
ASSET_SOURCE_RE = re.compile(r"^\s*source\s*:\s*[\"']?(?P<path>[^\n\"']+)", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TRANSCLUSION_RE = re.compile(r"!\[\[[^]]+]]")
MARKER_RE = re.compile(r"\{\{[^}]+}}")
HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)


def _severity(mode: str) -> str:
    return "warning" if mode == "draft" else "error"


def _location(note: Note, line: int = 1) -> SourceLocation:
    return SourceLocation(note.path, line)


def _resolved_transclusions(index: ProjectIndex, note: Note) -> list[Note]:
    resolved: list[Note] = []
    for link in note.links:
        if link.kind != "transclusion" or link.target.casefold().endswith(".pdf"):
            continue
        target, problem = index.resolve(link)
        if target is not None and problem is None:
            resolved.append(target)
    return resolved


def _reachable(index: ProjectIndex, root: Note) -> list[Note]:
    result: dict[Path, Note] = {}

    def visit(note: Note) -> None:
        if note.path in result:
            return
        result[note.path] = note
        for target in _resolved_transclusions(index, note):
            visit(target)

    visit(root)
    return list(result.values())


def _chapter_number(note: Note) -> int | None:
    match = CHAPTER_ID_RE.fullmatch(str(note.note_id or ""))
    return int(match["number"]) if match else None


def _validate_root_structure(index: ProjectIndex, root: Note, mode: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    children = _resolved_transclusions(index, root)
    positions: dict[str, int] = {}
    chapters: list[tuple[int, Note]] = []
    for position, note in enumerate(children):
        note_type = str(note.metadata.get("type", "")).casefold()
        note_id = str(note.note_id or "").casefold()
        if note_id in {"section:introduction", "section:conclusion", "section:references", "section:appendices"}:
            positions[note_id] = position
        if note_type == "chapter":
            number = _chapter_number(note)
            if number is None:
                diagnostics.append(Diagnostic(
                    "E_STRUCTURE_CHAPTER_ID", "chapter id must have the form 'chapter:N'",
                    _location(note), hint="Set front matter id, for example: chapter:1.",
                ))
            else:
                chapters.append((number, note))

    required = (
        ("section:introduction", "introduction"),
        ("section:conclusion", "conclusion"),
        ("section:references", "bibliography"),
        ("section:appendices", "appendices container"),
    )
    for note_id, label in required:
        if note_id not in positions:
            diagnostics.append(Diagnostic(
                "W_STRUCTURE_REQUIRED" if mode == "draft" else "E_STRUCTURE_REQUIRED",
                f"required {label} '{note_id}' is not included from the document root",
                _location(root), _severity(mode),
                f"Add a transclusion of the note with id '{note_id}' to the root document.",
            ))
    if not chapters:
        diagnostics.append(Diagnostic(
            "W_STRUCTURE_CHAPTERS" if mode == "draft" else "E_STRUCTURE_CHAPTERS",
            "the dissertation does not include any chapters", _location(root), _severity(mode),
            "Include chapter notes directly from the document root.",
        ))
        return diagnostics

    numbers = [number for number, _ in chapters]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        diagnostics.append(Diagnostic(
            "E_STRUCTURE_CHAPTER_ORDER", f"chapters must be consecutive and ordered: {expected}; found: {numbers}",
            _location(root), hint="Reorder chapter transclusions and use ids chapter:1, chapter:2, ...",
        ))

    intro = positions.get("section:introduction", -1)
    conclusion = positions.get("section:conclusion", len(children))
    references = positions.get("section:references", len(children) + 1)
    appendices = positions.get("section:appendices", len(children) + 2)
    chapter_positions = [children.index(note) for _, note in chapters]
    if not (intro < min(chapter_positions) <= max(chapter_positions) < conclusion < references < appendices):
        diagnostics.append(Diagnostic(
            "E_STRUCTURE_ORDER",
            "expected order: introduction, chapters, conclusion, bibliography, appendices",
            _location(root), hint="Reorder direct transclusions in the root note.",
        ))
    return diagnostics


def _validate_chapter(index: ProjectIndex, chapter: Note, mode: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    number = _chapter_number(chapter)
    if number is None:
        return diagnostics
    children = _resolved_transclusions(index, chapter)
    types = [str(note.metadata.get("type", "")).casefold() for note in children]
    preambles = [i for i, value in enumerate(types) if value == "chapter-preamble"]
    conclusions = [i for i, value in enumerate(types) if value == "chapter-conclusions"]
    paragraphs = [(i, note) for i, note in enumerate(children) if types[i] == "paragraph"]
    severity = _severity(mode)
    code_prefix = "W" if mode == "draft" else "E"

    if preambles != [0]:
        diagnostics.append(Diagnostic(
            f"{code_prefix}_CHAPTER_PREAMBLE", f"chapter {number} must start with exactly one chapter-preamble",
            _location(chapter), severity, "Add or move the chapter preamble to the first transclusion.",
        ))
    if not paragraphs:
        diagnostics.append(Diagnostic(
            f"{code_prefix}_CHAPTER_PARAGRAPHS", f"chapter {number} does not contain paragraph notes",
            _location(chapter), severity, "Include at least one note with type: paragraph.",
        ))
    else:
        actual: list[int] = []
        for _, paragraph in paragraphs:
            match = PARAGRAPH_ID_RE.fullmatch(str(paragraph.note_id or ""))
            if match is None or int(match["chapter"]) != number:
                diagnostics.append(Diagnostic(
                    "E_PARAGRAPH_ID", f"paragraph in chapter {number} must use id paragraph:{number}.N",
                    _location(paragraph), hint=f"Set a stable id such as paragraph:{number}.1.",
                ))
                continue
            actual.append(int(match["number"]))
        expected = list(range(1, len(actual) + 1))
        if actual != expected:
            diagnostics.append(Diagnostic(
                "E_PARAGRAPH_ORDER", f"chapter {number} paragraphs must be consecutive: {expected}; found: {actual}",
                _location(chapter), hint="Reorder paragraph transclusions and correct their stable ids.",
            ))
    if conclusions != ([len(children) - 1] if children else []):
        diagnostics.append(Diagnostic(
            f"{code_prefix}_CHAPTER_CONCLUSIONS", f"chapter {number} must end with exactly one chapter-conclusions note",
            _location(chapter), severity, "Add or move the chapter conclusions to the last transclusion.",
        ))
    return diagnostics


def _validate_notes(
    notes: list[Note], mode: str, placeholder_markers: tuple[str, ...], allowed_metadata: set[str],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    severity = _severity(mode)
    prefix = "W" if mode == "draft" else "E"
    for note in notes:
        unknown = sorted(set(note.metadata) - KNOWN_METADATA - allowed_metadata)
        for key in unknown:
            diagnostics.append(Diagnostic(
                "E_METADATA_UNKNOWN", f"unknown front matter key '{key}'", _location(note),
                hint=f"Remove '{key}' or add it to validation.allowed_metadata if it is intentional.",
            ))
        has_children = any(link.kind == "transclusion" for link in note.links)
        meaningful = COMMENT_RE.sub("", note.body)
        meaningful = TRANSCLUSION_RE.sub("", meaningful)
        meaningful = MARKER_RE.sub("", meaningful)
        meaningful = HEADING_RE.sub("", meaningful).strip()
        note_type = str(note.metadata.get("type", "")).casefold()
        if not has_children and note_type not in CONTAINER_TYPES and not meaningful:
            diagnostics.append(Diagnostic(
                f"{prefix}_NOTE_EMPTY", "leaf note has no text or supported content", _location(note), severity,
                "Add content or remove the note from the document graph.",
            ))
        folded = note.body.casefold()
        marker = next((item for item in placeholder_markers if item.casefold() in folded), None)
        if marker:
            source_text = note.path.read_text(encoding="utf-8-sig")
            source_folded = source_text.casefold()
            line = source_folded[: source_folded.index(marker.casefold())].count("\n") + 1
            diagnostics.append(Diagnostic(
                f"{prefix}_PLACEHOLDER", f"placeholder marker remains in document text: '{marker}'",
                _location(note, line), severity, "Replace the placeholder before a final build.",
            ))
    return diagnostics


def _validate_unused_assets(index: ProjectIndex, notes: list[Note]) -> list[Diagnostic]:
    assets_dir = index.content_dir / "assets"
    if not assets_dir.is_dir():
        return []
    used: set[Path] = set()
    for note in notes:
        for link in note.links:
            if link.kind == "transclusion" and not link.target.casefold().endswith(".md"):
                used.add((index.content_dir / link.target).resolve())
        for match in ASSET_SOURCE_RE.finditer(note.body):
            used.add((index.content_dir / match["path"].strip()).resolve())
    return [
        Diagnostic(
            "W_ASSET_UNUSED", "resource is not referenced by the dissertation graph",
            SourceLocation(path, 1), "warning", "Remove it or reference it from a reachable note.",
        )
        for path in sorted(assets_dir.rglob("*"))
        if path.is_file() and path.resolve() not in used
    ]


def validate_dissertation(
    index: ProjectIndex,
    root_note: Path | None,
    mode: str,
    *,
    placeholder_markers: tuple[str, ...] = DEFAULT_PLACEHOLDER_MARKERS,
    allowed_metadata: set[str] | None = None,
) -> list[Diagnostic]:
    if root_note is None:
        return []
    root = next((note for note in index.notes if note.path.resolve() == root_note.resolve()), None)
    if root is None or str(root.metadata.get("type", "")).casefold() != "document":
        return []
    reachable = _reachable(index, root)
    diagnostics = _validate_root_structure(index, root, mode)
    for note in reachable:
        if str(note.metadata.get("type", "")).casefold() == "chapter":
            diagnostics.extend(_validate_chapter(index, note, mode))
    diagnostics.extend(_validate_notes(reachable, mode, placeholder_markers, allowed_metadata or set()))
    diagnostics.extend(_validate_unused_assets(index, reachable))
    return diagnostics
