from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import yaml

from .config import load_document_config
from .assembler import assemble_note
from .assets import process_assets
from .docx_renderer import render_docx
from .print_release import archive_previous_release_pdfs, build_print_release
from .resolver import build_index
from .scaffold import scaffold
from .statistics import (
    measure_docx_pages,
    read_statistics_manifest,
    source_statistics,
    write_statistics_manifest,
)
from .validator import validate_index


VERSIONED_DOCX_RE = re.compile(r"^(?P<base>.+?)(?:-v(?P<version>\d+))?\.docx$", re.IGNORECASE)


def next_versioned_output(configured_output: Path) -> Path:
    match = VERSIONED_DOCX_RE.match(configured_output.name)
    if match is None:
        raise ValueError(f"output must be a DOCX file: {configured_output}")
    base = match["base"]
    versions = [int(match["version"] or 0)]
    for directory in (configured_output.parent, configured_output.parent / ".old"):
        if not directory.is_dir():
            continue
        for candidate in directory.glob(f"{base}-v*.docx"):
            candidate_match = VERSIONED_DOCX_RE.match(candidate.name)
            if candidate_match and candidate_match["base"].casefold() == base.casefold():
                versions.append(int(candidate_match["version"] or 0))
    return configured_output.with_name(f"{base}-v{max(versions) + 1}.docx")


def archive_previous_versions(current_output: Path) -> list[Path]:
    match = VERSIONED_DOCX_RE.match(current_output.name)
    if match is None:
        return []
    base = match["base"]
    archive = current_output.parent / ".old"
    archived: list[Path] = []
    candidates = sorted(current_output.parent.glob(f"{base}-v*.docx"))
    for candidate in candidates:
        if candidate == current_output:
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / candidate.name
        try:
            candidate.replace(destination)
        except OSError as exc:
            print(f"WARNING W_ARCHIVE_LOCKED: could not archive {candidate.name}: {exc}")
            continue
        archived.append(destination)
    return archived


def _document_validation_mode(project_root: Path, override: str | None = None) -> str:
    path = project_root / "config" / "document.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    validation = data.get("validation", {})
    if not isinstance(validation, dict):
        raise ValueError("document validation configuration must be a mapping")
    mode = override or str(validation.get("mode", "draft"))
    if mode not in {"draft", "final"}:
        raise ValueError(f"unknown dissertation validation mode: {mode}")
    return mode


def validate(project_root: Path, validation_mode: str | None = None) -> int:
    try:
        config = load_document_config(project_root)
        mode = _document_validation_mode(project_root, validation_mode)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(
        index, config.root_note, config.bibliography, config.publications_bibliography,
        config.conferences_bibliography, validation_mode=mode,
    )
    for diagnostic in diagnostics:
        print(diagnostic.format(project_root))
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = sum(item.severity == "warning" for item in diagnostics)
    print(f"Validated dissertation in {mode} mode ({len(index.notes)} notes): {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def _load_abstract_config(project_root: Path) -> dict:
    path = project_root / "config" / "abstract.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"abstract configuration must be a mapping: {path}")
    return data


def _abstract_validation_mode(abstract_data: dict, override: str | None = None) -> str:
    validation = abstract_data.get("validation", {})
    if not isinstance(validation, dict):
        raise ValueError("abstract.validation must be a mapping")
    mode = override or str(validation.get("mode", "draft"))
    if mode not in {"draft", "final"}:
        raise ValueError(f"unknown abstract validation mode: {mode}")
    return mode


def _abstract_research_overrides(project_root: Path) -> dict[str, str]:
    path = project_root / "config" / "research.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    research = data.get("research", {})
    selections = data.get("abstract_sources", {})
    if not isinstance(research, dict) or not isinstance(selections, dict):
        raise ValueError("research and abstract_sources must be mappings")
    overrides: dict[str, str] = {}
    for role, selection in selections.items():
        canonical = str(research.get(role, "")).strip()
        selected = str(selection).strip()
        if canonical and selected and selected.casefold() != "shared":
            overrides[canonical.casefold()] = selected
    return overrides


def validate_abstract(project_root: Path, validation_mode: str | None = None) -> int:
    try:
        config = load_document_config(project_root)
        abstract_data = _load_abstract_config(project_root)
        mode = _abstract_validation_mode(abstract_data, validation_mode)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    document = abstract_data.get("document", {})
    if not isinstance(document, dict):
        print("ERROR E_CONFIG: abstract.document must be a mapping")
        return 1
    root_path = project_root / str(document.get("root", "content/80 Abstract/Автореферат.md"))
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(
        index, root_path, config.bibliography, config.publications_bibliography,
        config.conferences_bibliography, validation_mode=mode,
    )
    for diagnostic in diagnostics:
        print(diagnostic.format(project_root))
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = sum(item.severity == "warning" for item in diagnostics)
    print(f"Validated abstract in {mode} mode: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def assemble(project_root: Path, validation_mode: str | None = None) -> int:
    try:
        config = load_document_config(project_root)
        mode = _document_validation_mode(project_root, validation_mode)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(
        index, config.root_note, config.bibliography, config.publications_bibliography,
        config.conferences_bibliography, validation_mode=mode,
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        for diagnostic in diagnostics:
            print(diagnostic.format(project_root))
        print("Assembly stopped because validation failed")
        return 1
    root_note = next((note for note in index.notes if note.path.resolve() == config.root_note.resolve()), None)
    if root_note is None:
        print(f"ERROR E_ROOT_MISSING: entry document was not indexed: {config.root_note}")
        return 1
    try:
        output = process_assets(
            assemble_note(index, root_note),
            config.content_dir,
            config.bibliography,
            config.publications_bibliography,
            config.conferences_bibliography,
        )
    except ValueError as exc:
        print(f"ERROR E_ASSET: {exc}")
        return 1
    config.assembled_markdown.parent.mkdir(parents=True, exist_ok=True)
    config.assembled_markdown.write_text(output, encoding="utf-8")
    print(f"Assembled Markdown: {config.assembled_markdown.relative_to(project_root)}")
    return 0


def _record_dissertation_statistics(project_root: Path, config, index, output: Path) -> Path:
    abstract_data = _load_abstract_config(project_root)
    statistics_config = abstract_data.get("statistics", {})
    if not isinstance(statistics_config, dict):
        raise ValueError("abstract.statistics must be a mapping")
    pdf_value = statistics_config.get("dissertation_pdf")
    pdf = project_root / str(pdf_value) if pdf_value else None
    stats = source_statistics(
        index, config.bibliography, config.publications_bibliography,
        config.conferences_bibliography,
    )
    stats["pages"] = measure_docx_pages(output, pdf)
    return write_statistics_manifest(project_root, output, stats)


def build(
    project_root: Path,
    require_statistics: bool = False,
    validation_mode: str | None = None,
) -> int:
    assembled = assemble(project_root, validation_mode)
    if assembled:
        return assembled
    config = load_document_config(project_root)
    try:
        versioned_output = next_versioned_output(config.output)
        output = render_docx(
            project_root,
            config.assembled_markdown,
            versioned_output,
            bibliography=config.bibliography,
            publications_bibliography=config.publications_bibliography,
            csl=config.csl,
            bibliography_title=config.bibliography_title,
        )
    except (RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR E_RENDER: {exc}")
        return 1
    archived = archive_previous_versions(output)
    print(f"Built DOCX: {output.relative_to(project_root)}")
    if archived:
        print(f"Archived {len(archived)} previous version(s) to {output.parent.name}\\.old")
    index = build_index(project_root, config.content_dir)
    try:
        manifest = _record_dissertation_statistics(project_root, config, index, output)
        print(f"Recorded dissertation statistics: {manifest.relative_to(project_root)}")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        level = "ERROR" if require_statistics else "WARNING"
        print(f"{level} E_STATISTICS_PAGES: {exc}")
        if require_statistics:
            return 1
    return 0


def assemble_abstract(project_root: Path, validation_mode: str | None = None) -> int:
    config = load_document_config(project_root)
    abstract_data = _load_abstract_config(project_root)
    mode = _abstract_validation_mode(abstract_data, validation_mode)
    document = abstract_data.get("document", {})
    root_path = project_root / str(document.get("root", "content/80 Abstract/Автореферат.md"))
    output_path = project_root / str(document.get("assembled_markdown", "build/abstract-assembled.md"))
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(
        index, root_path, config.bibliography, config.publications_bibliography,
        config.conferences_bibliography, validation_mode=mode,
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        for diagnostic in diagnostics:
            print(diagnostic.format(project_root))
        return 1
    root_note = next((note for note in index.notes if note.path.resolve() == root_path.resolve()), None)
    if root_note is None:
        print(f"ERROR E_ABSTRACT_ROOT: {root_path}")
        return 1
    try:
        _, stats = read_statistics_manifest(project_root)
    except ValueError as exc:
        print(f"ERROR E_ABSTRACT_STATISTICS: {exc}")
        return 1
    try:
        assembled = process_assets(
            assemble_note(
                index, root_note,
                source_overrides=_abstract_research_overrides(project_root),
            ),
            config.content_dir, config.bibliography,
            config.publications_bibliography, config.conferences_bibliography,
            statistics_override=stats,
            object_numbering=str(abstract_data.get("numbering", {}).get("objects", "global")),
            publication_config=abstract_data.get("publications"),
        )
    except ValueError as exc:
        print(f"ERROR E_ABSTRACT_ASSET: {exc}")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(assembled, encoding="utf-8")
    print(f"Assembled abstract: {output_path.relative_to(project_root)}")
    return 0


def build_abstract(project_root: Path, validation_mode: str | None = None) -> int:
    if assemble_abstract(project_root, validation_mode):
        return 1
    config = load_document_config(project_root)
    abstract_data = _load_abstract_config(project_root)
    document = abstract_data.get("document", {})
    assembled = project_root / str(document.get("assembled_markdown", "build/abstract-assembled.md"))
    configured_output = project_root / str(document.get("output", "build/abstract.docx"))
    try:
        output = render_docx(
            project_root, assembled, next_versioned_output(configured_output),
            publications_bibliography=config.publications_bibliography,
            csl=config.csl, bibliography_title="СПИСОК ОСНОВНЫХ ПУБЛИКАЦИЙ",
            document_kind="abstract",
        )
    except (RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR E_ABSTRACT_RENDER: {exc}")
        return 1
    archived = archive_previous_versions(output)
    print(f"Built abstract DOCX: {output.relative_to(project_root)}")
    if archived:
        print(f"Archived {len(archived)} previous abstract version(s)")
    return 0


def build_abstract_release(project_root: Path, validation_mode: str | None = None) -> int:
    if build_abstract(project_root, validation_mode):
        return 1
    abstract_data = _load_abstract_config(project_root)
    document = abstract_data.get("document", {})
    configured_output = project_root / str(document.get("output", "build/abstract.docx"))
    match = VERSIONED_DOCX_RE.match(configured_output.name)
    if match is None:
        print(f"ERROR E_ABSTRACT_RELEASE: output must be a DOCX file: {configured_output}")
        return 1
    candidates = list(configured_output.parent.glob(f"{match['base']}-v*.docx"))
    if not candidates:
        print("ERROR E_ABSTRACT_RELEASE: built abstract DOCX was not found")
        return 1
    current = max(candidates, key=lambda path: int(VERSIONED_DOCX_RE.match(path.name)["version"]))
    try:
        pdf, booklet = build_print_release(current)
        archived = archive_previous_release_pdfs(current)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR E_ABSTRACT_RELEASE: {exc}")
        return 1
    print(f"Built abstract PDF: {pdf.relative_to(project_root)}")
    print(f"Built abstract booklet: {booklet.relative_to(project_root)}")
    if archived:
        print(f"Archived {len(archived)} previous abstract PDF(s)")
    return 0


def build_all(project_root: Path, validation_mode: str | None = None) -> int:
    if build(project_root, require_statistics=True, validation_mode=validation_mode):
        return 1
    return build_abstract(project_root, validation_mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2docx")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    dissertation_validator = subparsers.add_parser("validate", help="validate source graph")
    dissertation_validator.add_argument("--mode", choices=("draft", "final"))
    abstract_validator = subparsers.add_parser("validate-abstract", help="validate abstract source graph")
    abstract_validator.add_argument("--mode", choices=("draft", "final"))
    dissertation_assembler = subparsers.add_parser("assemble", help="expand transclusions into Markdown")
    dissertation_assembler.add_argument("--mode", choices=("draft", "final"))
    dissertation_builder = subparsers.add_parser("build", help="assemble and render DOCX")
    dissertation_builder.add_argument("--mode", choices=("draft", "final"))
    abstract_assembler = subparsers.add_parser("assemble-abstract", help="assemble abstract Markdown")
    abstract_assembler.add_argument("--mode", choices=("draft", "final"))
    abstract_builder = subparsers.add_parser("build-abstract", help="assemble and render abstract DOCX")
    abstract_builder.add_argument("--mode", choices=("draft", "final"))
    release_builder = subparsers.add_parser("build-abstract-release", help="build abstract DOCX, A5 PDF and A4 booklet")
    release_builder.add_argument("--mode", choices=("draft", "final"))
    full_builder = subparsers.add_parser("build-all", help="build dissertation, collect final statistics, then build abstract")
    full_builder.add_argument("--mode", choices=("draft", "final"))
    subparsers.add_parser("scaffold", help="create chapter and paragraph Markdown structure")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate(args.project.resolve(), args.mode)
    if args.command == "validate-abstract":
        return validate_abstract(args.project.resolve(), args.mode)
    if args.command == "assemble":
        return assemble(args.project.resolve(), args.mode)
    if args.command == "build":
        return build(args.project.resolve(), validation_mode=args.mode)
    if args.command == "assemble-abstract":
        return assemble_abstract(args.project.resolve(), args.mode)
    if args.command == "build-abstract":
        return build_abstract(args.project.resolve(), args.mode)
    if args.command == "build-abstract-release":
        return build_abstract_release(args.project.resolve(), args.mode)
    if args.command == "build-all":
        return build_all(args.project.resolve(), args.mode)
    if args.command == "scaffold":
        try:
            created, chapters = scaffold(args.project.resolve())
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            print(f"ERROR E_SCAFFOLD: {exc}")
            return 1
        print(f"Scaffolded {chapters} chapter(s): {created} new file(s)")
        return 0
    parser.error(f"unknown command: {args.command}")
