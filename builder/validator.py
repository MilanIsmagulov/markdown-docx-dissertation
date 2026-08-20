from __future__ import annotations

import re
from pathlib import Path

import yaml

from .bibliography import BibEntry, completed_entries, read_bib_entries
from .model import Diagnostic, Note, SourceLocation
from .preflight import DEFAULT_PLACEHOLDER_MARKERS as DISSERTATION_PLACEHOLDERS
from .preflight import validate_dissertation
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
    validation_config, validation_problems = _dissertation_validation_options(index)
    diagnostics.extend(validation_problems)
    if not validation_problems:
        diagnostics.extend(
            validate_dissertation(
                index,
                root_note,
                validation_mode,
                placeholder_markers=validation_config["placeholder_markers"],
                allowed_metadata=validation_config["allowed_metadata"],
            )
        )
    diagnostics.extend(_validate_reachability(index, root_note))
    return diagnostics


def _dissertation_validation_options(index: ProjectIndex) -> tuple[dict, list[Diagnostic]]:
    path = index.root / "config" / "document.yaml"
    if not path.is_file():
        return {
            "placeholder_markers": DISSERTATION_PLACEHOLDERS,
            "allowed_metadata": set(),
        }, []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:
        return {}, [Diagnostic("E_VALIDATION_CONFIG", f"invalid document configuration: {exc}")]
    validation = data.get("validation", {})
    if not isinstance(validation, dict):
        return {}, [Diagnostic("E_VALIDATION_CONFIG", "validation must be a mapping")]
    markers = validation.get("placeholder_markers", list(DISSERTATION_PLACEHOLDERS))
    allowed = validation.get("allowed_metadata", [])
    diagnostics: list[Diagnostic] = []
    if not isinstance(markers, list) or not all(isinstance(item, str) and item for item in markers):
        diagnostics.append(Diagnostic("E_VALIDATION_CONFIG", "validation.placeholder_markers must be a list of strings"))
        markers = list(DISSERTATION_PLACEHOLDERS)
    if not isinstance(allowed, list) or not all(isinstance(item, str) and item for item in allowed):
        diagnostics.append(Diagnostic("E_VALIDATION_CONFIG", "validation.allowed_metadata must be a list of strings"))
        allowed = []
    return {"placeholder_markers": tuple(markers), "allowed_metadata": set(allowed)}, diagnostics


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

    publication_config = abstract.get("publications", {})
    if not isinstance(publication_config, dict):
        diagnostics.append(Diagnostic("E_ABSTRACT_PUBLICATION_CONFIG", "abstract.publications must be a mapping"))
    else:
        groups = publication_config.get("groups", [])
        if not isinstance(groups, list):
            diagnostics.append(Diagnostic("E_ABSTRACT_PUBLICATION_CONFIG", "abstract.publications.groups must be a list"))
        else:
            group_ids: set[str] = set()
            for position, group in enumerate(groups, 1):
                if not isinstance(group, dict) or not str(group.get("id", "")).strip() or not str(group.get("title", "")).strip():
                    diagnostics.append(Diagnostic("E_ABSTRACT_PUBLICATION_CONFIG", f"publication group {position} requires id and title"))
                    continue
                group_id = str(group["id"]).strip().casefold()
                if group_id in group_ids:
                    diagnostics.append(Diagnostic("E_ABSTRACT_PUBLICATION_CONFIG", f"duplicate publication group id '{group_id}'"))
                group_ids.add(group_id)
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
    research_roles = research.get("research", {})
    abstract_sources = research.get("abstract_sources", {})
    if not isinstance(research_roles, dict) or not isinstance(abstract_sources, dict):
        diagnostics.append(Diagnostic("E_ABSTRACT_RESEARCH_CONFIG", "research and abstract_sources must be mappings"))
        research_roles, abstract_sources = {}, {}
    reachable_paths = {note.path for note in reachable}
    for role, selection in abstract_sources.items():
        canonical_id = str(research_roles.get(role, "")).strip()
        if not canonical_id:
            diagnostics.append(Diagnostic("E_ABSTRACT_RESEARCH_ROLE", f"unknown research role '{role}'"))
            continue
        canonical = index.by_id.get(canonical_id.casefold())
        if canonical is not None and canonical.path not in reachable_paths:
            diagnostics.append(
                Diagnostic("E_ABSTRACT_RESEARCH_NOT_INCLUDED", f"research role '{role}' is not included in the abstract")
            )
        selected_id = canonical_id if str(selection).strip().casefold() == "shared" else str(selection).strip()
        selected = index.by_id.get(selected_id.casefold())
        if selected is None:
            diagnostics.append(
                Diagnostic(
                    "E_ABSTRACT_RESEARCH_SOURCE_MISSING",
                    f"abstract source for '{role}' does not exist: '{selected_id}'",
                )
            )
            continue
        allowed_types = {"canonical-research-statement"} if selected_id == canonical_id else {
            "canonical-research-statement", "abstract-research-statement",
        }
        if str(selected.metadata.get("type", "")).casefold() not in allowed_types:
            diagnostics.append(
                Diagnostic(
                    "E_ABSTRACT_RESEARCH_SOURCE_TYPE",
                    f"abstract source '{selected_id}' has an unsupported note type",
                )
            )
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
        note = index.by_id.get(str(note_id).casefold())
        if note is None:
            diagnostics.append(
                Diagnostic("E_RESEARCH_SOURCE_MISSING", f"research.{role} points to missing note '{note_id}'")
            )
        elif str(note.metadata.get("type", "")).casefold() != "canonical-research-statement":
            diagnostics.append(
                Diagnostic("E_RESEARCH_SOURCE_TYPE", f"research.{role} source '{note_id}' must have type 'canonical-research-statement'")
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
    equation_symbols: dict[str, SourceLocation] = {}
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
                if kind == "table":
                    diagnostics.extend(_validate_table_config(config, source, location))
            else:
                from .assets import _equation_latex, _equation_where

                try:
                    _equation_latex(config)
                except ValueError as exc:
                    diagnostics.append(Diagnostic("E_EQUATION_CONFIG", f"equation '{object_id}': {exc}", location))
                declared = config.get("symbols", [])
                if declared is None:
                    declared = []
                if not isinstance(declared, list) or not all(str(symbol).strip() for symbol in declared):
                    diagnostics.append(Diagnostic("E_EQUATION_SYMBOLS", f"equation '{object_id}' symbols must be a list", location))
                    declared = []
                try:
                    _, where_symbols = _equation_where(config, _term_symbol_definitions(index.root))
                except ValueError as exc:
                    diagnostics.append(Diagnostic("E_EQUATION_WHERE", f"equation '{object_id}': {exc}", location))
                    where_symbols = []
                for symbol in [*[str(item).strip() for item in declared], *where_symbols]:
                    equation_symbols.setdefault(symbol, location)

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
    if equation_symbols:
        diagnostics.extend(_validate_equation_symbols(index.root, equation_symbols, report_unused_bibliography))
    return diagnostics


def _term_symbol_definitions(project_root: Path) -> dict[str, str]:
    path = project_root / "config" / "terms.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError:
        return {}
    entries = data.get("symbols", []) if isinstance(data, dict) else []
    return {
        str(entry.get("term", "")).strip(): str(entry.get("definition", "")).strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("term", "")).strip()
    }


def _validate_equation_symbols(
    project_root: Path, used: dict[str, SourceLocation], report_unused: bool,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    definitions = _term_symbol_definitions(project_root)
    terms_path = project_root / "config" / "terms.yaml"
    if not definitions:
        return [Diagnostic(
            "E_SYMBOLS_CONFIG", "equations declare symbols but config/terms.yaml has no symbol definitions",
            SourceLocation(terms_path, 1),
        )]
    raw = yaml.safe_load(terms_path.read_text(encoding="utf-8-sig")) or {}
    entries = raw.get("symbols", []) if isinstance(raw, dict) else []
    seen: set[str] = set()
    for entry in entries:
        symbol = str(entry.get("term", "")).strip() if isinstance(entry, dict) else ""
        if symbol and symbol in seen:
            diagnostics.append(Diagnostic(
                "E_SYMBOL_DUPLICATE", f"symbol '{symbol}' is defined more than once",
                SourceLocation(terms_path, 1),
            ))
        seen.add(symbol)
    for symbol, location in used.items():
        if symbol not in definitions or not definitions[symbol]:
            diagnostics.append(Diagnostic(
                "E_SYMBOL_UNDEFINED", f"equation symbol '{symbol}' has no definition in config/terms.yaml",
                location, hint=f"Add term: {symbol} to the symbols list.",
            ))
    if report_unused:
        for symbol in definitions.keys() - used.keys():
            diagnostics.append(Diagnostic(
                "W_SYMBOL_UNUSED", f"symbol '{symbol}' is not declared by a numbered equation",
                SourceLocation(terms_path, 1), "warning", "Remove it or add it to an equation symbols list.",
            ))
    return diagnostics


def _validate_table_config(config: dict, source: Path, location: SourceLocation) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if config.get("sheet") and source.suffix.casefold() != ".xlsx":
        diagnostics.append(Diagnostic("E_TABLE_SHEET", "sheet can only be used with XLSX tables", location))
    cell_range = config.get("range")
    if cell_range:
        try:
            from openpyxl.utils.cell import range_boundaries

            range_boundaries(str(cell_range))
        except (TypeError, ValueError):
            diagnostics.append(Diagnostic("E_TABLE_RANGE", f"invalid table range: '{cell_range}'", location))
    widths = config.get("widths")
    if widths is not None:
        if not isinstance(widths, list) or not widths:
            diagnostics.append(Diagnostic("E_TABLE_WIDTHS", "table widths must be a non-empty list", location))
        else:
            total = 0.0
            for value in widths:
                match = re.fullmatch(r"(\d+(?:\.\d+)?)mm", str(value).strip(), re.IGNORECASE)
                if match is None or float(match.group(1)) <= 0:
                    diagnostics.append(Diagnostic("E_TABLE_WIDTHS", f"invalid table column width: '{value}'", location))
                elif match:
                    total += float(match.group(1))
            if total > 175 and str(config.get("section", "portrait")).casefold() == "portrait":
                diagnostics.append(Diagnostic(
                    "W_TABLE_WIDE", f"declared table width is {total:g} mm and exceeds the portrait text area",
                    location, "warning", "Use section: auto or section: landscape.",
                ))
    align = config.get("align")
    if align is not None and (
        not isinstance(align, list)
        or any(str(value).casefold() not in {"left", "center", "right", "justify"} for value in align)
    ):
        diagnostics.append(Diagnostic("E_TABLE_ALIGN", "table align must contain left, center, right or justify", location))
    merges = config.get("merges")
    if merges is not None and (
        not isinstance(merges, list)
        or any(re.fullmatch(r"[A-Z]+\d+:[A-Z]+\d+", str(value).upper()) is None for value in merges)
    ):
        diagnostics.append(Diagnostic("E_TABLE_MERGES", "table merges must use ranges such as A1:B1", location))
    if "repeat_header" in config and not isinstance(config["repeat_header"], bool):
        diagnostics.append(Diagnostic("E_TABLE_REPEAT_HEADER", "repeat_header must be true or false", location))
    if "split_rows" in config and (
        not isinstance(config["split_rows"], int) or isinstance(config["split_rows"], bool) or config["split_rows"] < 1
    ):
        diagnostics.append(Diagnostic("E_TABLE_SPLIT", "split_rows must be a positive integer", location))
    if str(config.get("section", "portrait")).casefold() not in {"portrait", "auto", "landscape"}:
        diagnostics.append(Diagnostic("E_TABLE_SECTION", "table section must be portrait, auto or landscape", location))
    if source.is_file() and not any(item.code in {"E_TABLE_RANGE", "E_TABLE_WIDTHS", "E_TABLE_ALIGN", "E_TABLE_MERGES"} for item in diagnostics):
        try:
            from .assets import _read_rows

            rows = _read_rows(
                source,
                str(config["sheet"]) if config.get("sheet") else None,
                str(config["range"]) if config.get("range") else None,
            )
        except ValueError as exc:
            diagnostics.append(Diagnostic("E_TABLE_IMPORT", str(exc), location))
        else:
            columns = max((len(row) for row in rows), default=0)
            if widths is not None and len(widths) != columns:
                diagnostics.append(Diagnostic(
                    "E_TABLE_WIDTHS", f"table has {columns} columns but {len(widths)} widths were declared", location,
                ))
            if align is not None and len(align) != columns:
                diagnostics.append(Diagnostic(
                    "E_TABLE_ALIGN", f"table has {columns} columns but {len(align)} alignments were declared", location,
                ))
            if isinstance(merges, list):
                from openpyxl.utils.cell import range_boundaries

                for merged_range in merges:
                    min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
                    if min_col < 1 or min_row < 1 or max_col > columns or max_row > len(rows):
                        diagnostics.append(Diagnostic(
                            "E_TABLE_MERGES", f"merged range is outside the selected table: '{merged_range}'", location,
                        ))
    return diagnostics


def _validate_bibliography_entries(entries: list[BibEntry]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    required = {
        "article": ({"author", "title", "year"}, ({"journal", "eprint"},)),
        "inproceedings": ({"author", "title", "year", "booktitle"}, ()),
        "conference": ({"author", "title", "year", "booktitle"}, ()),
        "book": ({"title", "year", "publisher"}, ({"author", "editor"},)),
        "online": ({"title", "url", "urldate"}, ()),
        "www": ({"title", "url", "urldate"}, ()),
        "electronic": ({"title", "url", "urldate"}, ()),
        "standard": ({"title", "year", "number"}, ()),
        "patent": ({"author", "title", "year", "number"}, ()),
        "phdthesis": ({"author", "title", "year", "school"}, ()),
        "mastersthesis": ({"author", "title", "year", "school"}, ()),
        "techreport": ({"author", "title", "year", "institution"}, ()),
        "software": ({"author", "title", "year", "number"}, ()),
    }
    seen_keys: set[str] = set()
    identifiers: dict[tuple[str, str], str] = {}
    records: dict[tuple[str, str, str], str] = {}
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
        fingerprint = tuple(
            re.sub(r"[^\w]+", "", entry.fields.get(field, "").casefold())
            for field in ("author", "title", "year")
        )
        if fingerprint[1] and fingerprint in records:
            diagnostics.append(
                Diagnostic("E_BIB_RECORD_DUPLICATE", f"duplicate bibliographic record in '{records[fingerprint]}' and '{entry.key}'")
            )
        else:
            records[fingerprint] = entry.key
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
        and not any(part.startswith("_") for part in note.path.relative_to(index.content_dir).parts)
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
