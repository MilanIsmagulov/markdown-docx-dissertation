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
from .resolver import build_index
from .scaffold import scaffold
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


def validate(project_root: Path) -> int:
    try:
        config = load_document_config(project_root)
    except ValueError as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(index, config.root_note, config.bibliography, config.publications_bibliography, config.conferences_bibliography)
    for diagnostic in diagnostics:
        print(diagnostic.format(project_root))
    errors = sum(item.severity == "error" for item in diagnostics)
    warnings = sum(item.severity == "warning" for item in diagnostics)
    print(f"Validated {len(index.notes)} note(s): {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def assemble(project_root: Path) -> int:
    try:
        config = load_document_config(project_root)
    except ValueError as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(index, config.root_note, config.bibliography, config.publications_bibliography, config.conferences_bibliography)
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


def build(project_root: Path) -> int:
    assembled = assemble(project_root)
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
    return 0


def assemble_abstract(project_root: Path) -> int:
    config = load_document_config(project_root)
    abstract_data = yaml.safe_load((project_root / "config" / "abstract.yaml").read_text(encoding="utf-8-sig")) or {}
    document = abstract_data.get("document", {})
    root_path = project_root / str(document.get("root", "content/80 Abstract/Автореферат.md"))
    output_path = project_root / str(document.get("assembled_markdown", "build/abstract-assembled.md"))
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(index, root_path, config.bibliography, config.publications_bibliography, config.conferences_bibliography)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        for diagnostic in diagnostics:
            print(diagnostic.format(project_root))
        return 1
    root_note = next((note for note in index.notes if note.path.resolve() == root_path.resolve()), None)
    if root_note is None:
        print(f"ERROR E_ABSTRACT_ROOT: {root_path}")
        return 1
    metadata = yaml.safe_load((project_root / "config" / "metadata.yaml").read_text(encoding="utf-8-sig")) or {}
    structure = metadata.get("structure", {})
    source_text = "\n".join(note.body for note in index.notes if str(note.metadata.get("type", "")) != "abstract")
    stats = {
        "chapters": int(structure.get("chapters", 0)),
        "appendices": int(structure.get("appendices", 0)),
        "figures": len(re.findall(r"^```figure\s*$", source_text, re.MULTILINE)),
        "tables": len(re.findall(r"^```table\s*$", source_text, re.MULTILINE)),
    }
    try:
        assembled = process_assets(
            assemble_note(index, root_note), config.content_dir, config.bibliography,
            config.publications_bibliography, config.conferences_bibliography,
            statistics_override=stats,
        )
    except ValueError as exc:
        print(f"ERROR E_ABSTRACT_ASSET: {exc}")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(assembled, encoding="utf-8")
    print(f"Assembled abstract: {output_path.relative_to(project_root)}")
    return 0


def build_abstract(project_root: Path) -> int:
    if assemble_abstract(project_root):
        return 1
    config = load_document_config(project_root)
    abstract_data = yaml.safe_load((project_root / "config" / "abstract.yaml").read_text(encoding="utf-8-sig")) or {}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2docx")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate source graph")
    subparsers.add_parser("assemble", help="expand transclusions into Markdown")
    subparsers.add_parser("build", help="assemble and render DOCX")
    subparsers.add_parser("assemble-abstract", help="assemble abstract Markdown")
    subparsers.add_parser("build-abstract", help="assemble and render abstract DOCX")
    subparsers.add_parser("scaffold", help="create chapter and paragraph Markdown structure")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate(args.project.resolve())
    if args.command == "assemble":
        return assemble(args.project.resolve())
    if args.command == "build":
        return build(args.project.resolve())
    if args.command == "assemble-abstract":
        return assemble_abstract(args.project.resolve())
    if args.command == "build-abstract":
        return build_abstract(args.project.resolve())
    if args.command == "scaffold":
        try:
            created, chapters = scaffold(args.project.resolve())
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            print(f"ERROR E_SCAFFOLD: {exc}")
            return 1
        print(f"Scaffolded {chapters} chapter(s): {created} new file(s)")
        return 0
    parser.error(f"unknown command: {args.command}")
