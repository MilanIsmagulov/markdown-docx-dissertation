from __future__ import annotations

import argparse
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


def validate(project_root: Path) -> int:
    try:
        config = load_document_config(project_root)
    except ValueError as exc:
        print(f"ERROR E_CONFIG: {exc}")
        return 1
    index = build_index(project_root, config.content_dir)
    diagnostics = validate_index(index, config.root_note)
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
    diagnostics = validate_index(index, config.root_note)
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
        output = process_assets(assemble_note(index, root_note), config.content_dir)
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
        output = render_docx(project_root, config.assembled_markdown, config.output)
    except (RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR E_RENDER: {exc}")
        return 1
    print(f"Built DOCX: {output.relative_to(project_root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="md2docx")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate source graph")
    subparsers.add_parser("assemble", help="expand transclusions into Markdown")
    subparsers.add_parser("build", help="assemble and render DOCX")
    subparsers.add_parser("scaffold", help="create chapter and paragraph Markdown structure")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate(args.project.resolve())
    if args.command == "assemble":
        return assemble(args.project.resolve())
    if args.command == "build":
        return build(args.project.resolve())
    if args.command == "scaffold":
        try:
            created, chapters = scaffold(args.project.resolve())
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            print(f"ERROR E_SCAFFOLD: {exc}")
            return 1
        print(f"Scaffolded {chapters} chapter(s): {created} new file(s)")
        return 0
    parser.error(f"unknown command: {args.command}")
