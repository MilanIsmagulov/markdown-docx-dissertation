from __future__ import annotations

import re
from pathlib import Path

import yaml

from .bibliography import BibEntry, completed_entries, read_bib_entries
from .model import Diagnostic, Note, SourceLocation
from .resolver import ProjectIndex


DIRECTIVE_BLOCK_RE = re.compile(r"^```(?P<kind>table|figure|equation)\s*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL)
OBJECT_REFERENCE_RE = re.compile(r"\{\{(?:ref|number):(?P<kind>table|figure|equation):(?P<id>[A-Za-z0-9_.-]+)}}")
CITATION_RE = re.compile(r"(?<![\w@])@(?P<key>[A-Za-z0-9_:.+/-]+)")
BIB_KEY_RE = re.compile(r"@[A-Za-z]+\s*\{\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE)
SECTION_RE = re.compile(r"\{\{section:(?P<name>[a-z][a-z0-9_-]*)}}")
DEFAULT_PLACEHOLDER_MARKERS = ("[указать", "_____", "________", "20__")


def _heading_key(text: str) -> str:
    return " ".join(text.casefold().split())


def validate_index(
    index: ProjectIndex,
    root_note: Path | None = None,
    bibliography: Path | None = None,
    publications_bibliography: Path | None = None,
    conferences_bibliography: Path | None = None,
    validation_mode: str = "draft",
) -> list[Diagnostic]:
    if validation_mode not in {"draft", "final"}:
        return [Diagnostic("E_VALIDATION_MODE", f"unknown validation mode: '{validation_mode}'")]
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
                elif asset.stat().st_size == 0:
                    diagnostics.append(Diagnostic("E_PDF_EMPTY", f"PDF asset is empty: '{link.target}'", link.location))
                else:
                    if asset.stat().st_size > 50 * 1024 * 1024:
                        diagnostics.append(
                            Diagnostic("W_PDF_LARGE", f"PDF asset exceeds 50 MiB: '{link.target}'", link.location, "warning")
                        )
                    if link.heading and link.heading.casefold().startswith("page="):
                        try:
                            requested_page = int(link.heading.split("=", 1)[1])
                            from pypdf import PdfReader

                            page_count = len(PdfReader(asset).pages)
                            if requested_page < 1 or requested_page > page_count:
                                diagnostics.append(
                                    Diagnostic(
                                        "E_PDF_PAGE_RANGE",
                                        f"PDF page {requested_page} is outside 1..{page_count}: '{link.target}'",
                                        link.location,
                                    )
                                )
                        except (ValueError, OSError):
                            diagnostics.append(Diagnostic("E_PDF_PAGE", f"invalid PDF page selector: '{link.raw}'", link.location))
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
    selected_root = next(
        (note for note in index.notes if root_note is not None and note.path.resolve() == root_note.resolve()),
        None,
    )
    root_is_abstract = selected_root is not None and str(selected_root.metadata.get("type", "")).casefold() == "abstract"
    diagnostics.extend(
        _validate_document_sources(
            index,
            bibliography,
            publications_bibliography,
            notes=_reachable_notes(index, root_note),
            report_unused_bibliography=not root_is_abstract,
        )
    )
    diagnostics.extend(_validate_conferences(conferences_bibliography))
    diagnostics.extend(_validate_appendices(index))
    diagnostics.extend(_validate_sections(index))
    diagnostics.extend(_validate_semantic_sources(index))
    diagnostics.extend(_validate_abstract(index, root_note, validation_mode))
    diagnostics.extend(_validate_reachability(index, root_note))
    return diagnostics


def _read_yaml_mapping(path: Path, code: str) -> tuple[dict, list[Diagnostic]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except FileNotFoundError:
        return {}, [Diagnostic(code, f"configuration does not exist: {path}")]
    except yaml.YAMLError as exc:
        return {}, [Diagnostic(code, f"invalid YAML in {path}: {exc}")]
    if not isinstance(data, dict):
        return {}, [Diagnostic(code, f"configuration must be a mapping: {path}")]
    return data, []


def _nested_value(data: dict, dotted_key: str):
    value = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _validate_abstract(index: ProjectIndex, root_note: Path | None, mode: str) -> list[Diagnostic]:
    if root_note is None:
        return []
    root = next((note for note in index.notes if note.path.resolve() == root_note.resolve()), None)
    if root is None or str(root.metadata.get("type", "")).casefold() != "abstract":
        return []

    diagnostics: list[Diagnostic] = []
    metadata_path = index.root / "config" / "metadata.yaml"
    abstract_path = index.root / "config" / "abstract.yaml"
    research_path = index.root / "config" / "research.yaml"
    metadata, problems = _read_yaml_mapping(metadata_path, "E_ABSTRACT_METADATA_CONFIG")
    diagnostics.extend(problems)
    abstract, problems = _read_yaml_mapping(abstract_path, "E_ABSTRACT_CONFIG")
    diagnostics.extend(problems)
    research, problems = _read_yaml_mapping(research_path, "E_ABSTRACT_RESEARCH_CONFIG")
    diagnostics.extend(problems)
    if diagnostics:
        return diagnostics

    required_metadata = (
        "title", "degree", "author.full_name", "organization.full_name",
        "specialty.code", "specialty.name", "supervisor.full_name",
        "supervisor.degree", "supervisor.title", "city", "year",
    )
    required_defense = (
        "organization", "organization_unit", "leading_organization", "date", "time",
        "council", "address", "library", "website", "mailing_date", "secretary",
        "secretary_degree",
    )
    values: list[tuple[str, object, Path]] = []
    for key in required_metadata:
        value = _nested_value(metadata, key)
        values.append((f"metadata.{key}", value, metadata_path))
    for key in required_defense:
        value = _nested_value(abstract, f"defense.{key}")
        values.append((f"abstract.defense.{key}", value, abstract_path))
    opponents = _nested_value(abstract, "defense.opponents")
    if not isinstance(opponents, list) or not opponents:
        diagnostics.append(Diagnostic("E_ABSTRACT_METADATA_REQUIRED", "abstract.defense.opponents must contain at least one entry"))
    else:
        for index_number, opponent in enumerate(opponents, 1):
            for key in ("degree", "full_name"):
                value = opponent.get(key) if isinstance(opponent, dict) else None
                values.append((f"abstract.defense.opponents[{index_number}].{key}", value, abstract_path))

    validation = abstract.get("validation", {})
    if not isinstance(validation, dict):
        diagnostics.append(Diagnostic("E_ABSTRACT_VALIDATION_CONFIG", "abstract.validation must be a mapping"))
        validation = {}
    markers = validation.get("placeholder_markers", DEFAULT_PLACEHOLDER_MARKERS)
    if not isinstance(markers, list) or not all(isinstance(item, str) for item in markers):
        diagnostics.append(Diagnostic("E_ABSTRACT_VALIDATION_CONFIG", "placeholder_markers must be a list of strings"))
        markers = list(DEFAULT_PLACEHOLDER_MARKERS)
    allowed_placeholder_fields = validation.get("allowed_placeholder_fields", [])
    if not isinstance(allowed_placeholder_fields, list) or not all(isinstance(item, str) for item in allowed_placeholder_fields):
        diagnostics.append(Diagnostic("E_ABSTRACT_VALIDATION_CONFIG", "allowed_placeholder_fields must be a list of strings"))
        allowed_placeholder_fields = []
    allowed_placeholder_fields = set(allowed_placeholder_fields)
    for key, value, path in values:
        if value is None or (isinstance(value, str) and not value.strip()):
            diagnostics.append(Diagnostic("E_ABSTRACT_METADATA_REQUIRED", f"required value is missing: {key}", SourceLocation(path, 1)))
            continue
        rendered = str(value).casefold()
        if key not in allowed_placeholder_fields and any(marker.casefold() in rendered for marker in markers):
            severity = "error" if mode == "final" else "warning"
            diagnostics.append(Diagnostic("E_ABSTRACT_PLACEHOLDER" if mode == "final" else "W_ABSTRACT_PLACEHOLDER", f"placeholder remains in {key}", SourceLocation(path, 1), severity))

    expected_chapters = research.get("chapters", [])
    if not isinstance(expected_chapters, list):
        diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_CONFIG", "research.chapters must be a list"))
        expected_chapters = []
    expected_summaries = {
        str(item.get("id", "")).strip(): str(item.get("summary", "")).strip()
        for item in expected_chapters if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    expected_sources = set(expected_summaries)
    reachable = _reachable_notes(index, root_note)
    summaries = [note for note in reachable if str(note.metadata.get("type", "")).casefold() == "chapter-summary"]
    sources: dict[str, Note] = {}
    for summary in summaries:
        source = str(summary.metadata.get("source", "")).strip()
        if not source:
            diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_SOURCE", f"chapter summary '{summary.title}' has no source", SourceLocation(summary.path, 1)))
        elif source in sources:
            diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_SOURCE_DUPLICATE", f"chapter source '{source}' is used more than once", SourceLocation(summary.path, 1)))
        else:
            sources[source] = summary
            source_note = index.by_id.get(source.casefold())
            if source_note is not None and str(source_note.metadata.get("type", "")).casefold() != "chapter":
                diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_SOURCE_TYPE", f"source '{source}' must have type 'chapter'", SourceLocation(summary.path, 1)))
            expected_summary_id = expected_summaries.get(source)
            if expected_summary_id and summary.note_id != expected_summary_id:
                diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_SUMMARY_ID", f"source '{source}' expects summary id '{expected_summary_id}'", SourceLocation(summary.path, 1)))
    missing = sorted(expected_sources - sources.keys())
    unexpected = sorted(sources.keys() - expected_sources)
    if len(summaries) != len(expected_sources):
        diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_COUNT", f"expected {len(expected_sources)} chapter summaries, found {len(summaries)}", SourceLocation(root.path, 1)))
    for source in missing:
        diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_MISSING", f"chapter summary is missing for '{source}'", SourceLocation(root.path, 1)))
    for source in unexpected:
        diagnostics.append(Diagnostic("E_ABSTRACT_CHAPTER_UNEXPECTED", f"unexpected chapter summary source '{source}'", SourceLocation(sources[source].path, 1)))

    denylist = validation.get("denylist", [])
    public_profile = bool(validation.get("public_profile", False))
    if not isinstance(denylist, list) or not all(isinstance(item, str) and item.strip() for item in denylist):
        diagnostics.append(Diagnostic("E_ABSTRACT_VALIDATION_CONFIG", "denylist must be a list of non-empty strings"))
    elif public_profile:
        scanned = [metadata_path, abstract_path, *[note.path for note in reachable]]
        for token in denylist:
            for path in scanned:
                if token.casefold() in path.read_text(encoding="utf-8-sig").casefold():
                    diagnostics.append(Diagnostic("E_PUBLIC_DENYLIST", f"deny-listed value found in public profile: '{token}'", SourceLocation(path, 1)))
                    break
    return diagnostics


def _validate_conferences(path: Path | None) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for entry in read_bib_entries(path):
        if entry.entry_type != "conference":
            diagnostics.append(Diagnostic("E_CONFERENCE_TYPE", f"conference entry '{entry.key}' must use @conference"))
        missing = [name for name in ("title", "eventdate", "location", "talk") if not entry.fields.get(name)]
        if missing:
            diagnostics.append(
                Diagnostic("E_CONFERENCE_FIELDS", f"conference '{entry.key}' is missing: {', '.join(missing)}")
            )
    return diagnostics


def _validate_sections(index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    path = index.root / "config" / "sections.yaml"
    if not path.is_file():
        return diagnostics
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:
        return [Diagnostic("E_SECTIONS_YAML", f"invalid sections configuration: {exc}")]
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        return [Diagnostic("E_SECTIONS_CONFIG", "sections.profiles must be a mapping")]
    default_name = str(data.get("default", "")).strip()
    if default_name not in profiles:
        diagnostics.append(Diagnostic("E_SECTION_DEFAULT", f"default section profile does not exist: '{default_name}'"))
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            diagnostics.append(Diagnostic("E_SECTION_PROFILE", f"section profile '{name}' must be a mapping"))
            continue
        if str(profile.get("size", "A4")).casefold() != "a4":
            diagnostics.append(Diagnostic("E_SECTION_SIZE", f"section profile '{name}' must use A4"))
        if str(profile.get("orientation", "portrait")).casefold() not in {"portrait", "landscape"}:
            diagnostics.append(Diagnostic("E_SECTION_ORIENTATION", f"invalid orientation in section profile '{name}'"))
        margins = profile.get("margins", {})
        if not isinstance(margins, dict) or any(key not in margins for key in ("left", "right", "top", "bottom")):
            diagnostics.append(Diagnostic("E_SECTION_MARGINS", f"section profile '{name}' requires all four margins"))
    for note in index.notes:
        for match in SECTION_RE.finditer(note.body):
            if match["name"] not in profiles:
                diagnostics.append(
                    Diagnostic(
                        "E_SECTION_UNKNOWN",
                        f"unknown section profile '{match['name']}'",
                        SourceLocation(note.path, _line_for_offset(note.body, match.start())),
                    )
                )
    return diagnostics


def _validate_semantic_sources(index: ProjectIndex) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for note in index.notes:
        source = str(note.metadata.get("source", "")).strip()
        if source and source.casefold() not in index.by_id:
            diagnostics.append(
                Diagnostic(
                    "E_SEMANTIC_SOURCE_MISSING",
                    f"semantic source '{source}' declared by '{note.title}' does not exist",
                    SourceLocation(note.path, 1),
                )
            )
    registry_path = index.root / "config" / "research.yaml"
    if not registry_path.is_file():
        return diagnostics
    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:
        diagnostics.append(Diagnostic("E_RESEARCH_YAML", f"invalid research registry: {exc}"))
        return diagnostics
    research = registry.get("research", {})
    if not isinstance(research, dict):
        diagnostics.append(Diagnostic("E_RESEARCH_CONFIG", "research registry must be a mapping"))
        return diagnostics
    for role, note_id in research.items():
        if str(note_id).casefold() not in index.by_id:
            diagnostics.append(
                Diagnostic("E_RESEARCH_SOURCE_MISSING", f"research.{role} points to missing note '{note_id}'")
            )
    return diagnostics


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _validate_document_sources(
    index: ProjectIndex,
    bibliography: Path | None,
    publications_bibliography: Path | None = None,
    notes: list[Note] | None = None,
    report_unused_bibliography: bool = True,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    objects: dict[tuple[str, str], SourceLocation] = {}
    references: list[tuple[str, str, SourceLocation]] = []
    citations: dict[str, SourceLocation] = {}
    allowed_extensions = {"table": {".csv", ".tsv", ".xlsx"}, "figure": {".png", ".jpg", ".jpeg", ".svg"}}

    for note in index.notes if notes is None else notes:
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

    bibliography_entries = completed_entries(read_bib_entries(bibliography))
    publication_entries = completed_entries(read_bib_entries(publications_bibliography))
    all_entries = [*bibliography_entries, *publication_entries]
    if all_entries:
        bib_keys = {entry.key for entry in all_entries}
        for key, location in citations.items():
            if key not in bib_keys:
                diagnostics.append(Diagnostic("E_CITATION_MISSING", f"citation key '{key}' is absent from bibliography", location))
        for key in sorted(bib_keys - citations.keys()):
            if report_unused_bibliography and key in {entry.key for entry in bibliography_entries}:
                diagnostics.append(Diagnostic("W_BIB_UNUSED", f"bibliography entry '{key}' is not cited", severity="warning"))
        diagnostics.extend(_validate_bibliography_entries(all_entries))
    return diagnostics


def _validate_bibliography_entries(entries: list[BibEntry]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    required = {
        "article": ({"author", "title", "year"}, ({"journal", "eprint"},)),
        "inproceedings": ({"author", "title", "year", "booktitle"}, ()),
        "book": ({"title", "year", "publisher"}, ({"author", "editor"},)),
        "online": ({"title", "url", "urldate"}, ()),
        "www": ({"title", "url", "urldate"}, ()),
        "standard": ({"title", "year", "number"}, ()),
        "patent": ({"author", "title", "year", "number"}, ()),
        "phdthesis": ({"author", "title", "year", "school"}, ()),
        "mastersthesis": ({"author", "title", "year", "school"}, ()),
        "techreport": ({"author", "title", "year", "institution"}, ()),
        "software": ({"author", "title", "year", "number"}, ()),
    }
    seen_keys: set[str] = set()
    identifiers: dict[tuple[str, str], str] = {}
    for entry in entries:
        if entry.key.casefold() in seen_keys:
            diagnostics.append(Diagnostic("E_BIB_KEY_DUPLICATE", f"duplicate bibliography key '{entry.key}'"))
        seen_keys.add(entry.key.casefold())
        specification = required.get(entry.entry_type)
        if specification:
            mandatory, alternatives = specification
            missing = sorted(field for field in mandatory if not entry.fields.get(field))
            for group in alternatives:
                if not any(entry.fields.get(field) for field in group):
                    missing.append("/".join(sorted(group)))
            if missing:
                diagnostics.append(
                    Diagnostic("E_BIB_FIELD_MISSING", f"{entry.key} ({entry.entry_type}) misses: {', '.join(missing)}")
                )
        if entry.fields.get("url") and not entry.fields.get("urldate"):
            diagnostics.append(Diagnostic("E_BIB_ACCESS_DATE_MISSING", f"URL entry '{entry.key}' requires urldate"))
        for field in ("doi", "isbn"):
            value = re.sub(r"[^a-z0-9]", "", entry.fields.get(field, "").casefold())
            if not value:
                continue
            identifier = (field, value)
            if identifier in identifiers:
                diagnostics.append(
                    Diagnostic("E_BIB_IDENTIFIER_DUPLICATE", f"duplicate {field.upper()} in '{identifiers[identifier]}' and '{entry.key}'")
                )
            else:
                identifiers[identifier] = entry.key
    return diagnostics


def _validate_appendices(index: ProjectIndex) -> list[Diagnostic]:
    labels: list[str] = []
    for note in index.notes:
        if str(note.metadata.get("type", "")).casefold() != "appendix":
            continue
        match = re.search(r"Приложение\s+([А-Я])", note.title, re.IGNORECASE)
        if match:
            labels.append(match.group(1).upper())
    expected_letters = list("АБВГДЕЖИКЛМНПРСТУФХЦШЩЭЮЯ")[: len(labels)]
    if sorted(labels, key=lambda item: expected_letters.index(item) if item in expected_letters else 999) != expected_letters:
        return [Diagnostic("E_APPENDIX_ORDER", f"appendices must be consecutive: {', '.join(expected_letters)}")]
    return []


def _validate_reachability(index: ProjectIndex, root_note: Path | None) -> list[Diagnostic]:
    if root_note is None or not root_note.is_file():
        return []
    root = next((note for note in index.notes if note.path.resolve() == root_note.resolve()), None)
    if root is None:
        return []
    reachable = {note.path for note in _reachable_notes(index, root_note)}
    if str(root.metadata.get("type", "")).casefold() == "abstract":
        relevant_types = {"abstract", "chapter-summary"}
        return [
            Diagnostic("W_NOTE_ORPHAN", "note is not included from the root document", SourceLocation(note.path, 1), "warning")
            for note in index.notes
            if note.path not in reachable
            and str(note.metadata.get("type", "")).casefold() in relevant_types
        ]
    library_types = {"canonical-research-statement"}
    alternate_document_types = {"abstract", "chapter-summary"}
    return [
        Diagnostic("W_NOTE_ORPHAN", "note is not included from the root document", SourceLocation(note.path, 1), "warning")
        for note in index.notes
        if note.path not in reachable
        and not note.path.name.startswith("_")
        and str(note.metadata.get("type", "")) not in library_types | alternate_document_types
    ]


def _reachable_notes(index: ProjectIndex, root_note: Path | None) -> list[Note]:
    if root_note is None or not root_note.is_file():
        return list(index.notes)
    root = next((note for note in index.notes if note.path.resolve() == root_note.resolve()), None)
    if root is None:
        return list(index.notes)
    reachable: dict[Path, Note] = {}

    def visit(note: Note) -> None:
        if note.path in reachable:
            return
        reachable[note.path] = note
        for link in note.links:
            if link.kind != "transclusion" or link.target.casefold().endswith(".pdf"):
                continue
            target, problem = index.resolve(link)
            if target is not None and problem is None:
                visit(target)

    visit(root)
    return list(reachable.values())


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
