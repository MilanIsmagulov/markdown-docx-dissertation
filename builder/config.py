from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class DocumentConfig:
    project_root: Path
    content_dir: Path
    root_note: Path
    output: Path
    assembled_markdown: Path
    bibliography: Path | None
    publications_bibliography: Path | None
    csl: Path | None
    bibliography_title: str


def load_document_config(project_root: Path) -> DocumentConfig:
    path = project_root / "config" / "document.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"missing configuration: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    document = data.get("document", {})
    if not isinstance(document, dict):
        raise ValueError("'document' must be a mapping")
    content_dir = project_root / str(document.get("content", "content"))
    root_note = project_root / str(document.get("root", "content/root.md"))
    output = project_root / str(document.get("output", "build/dissertation.docx"))
    assembled_markdown = project_root / str(document.get("assembled_markdown", "build/assembled.md"))
    bibliography_value = document.get("bibliography")
    publications_value = document.get("publications_bibliography")
    csl_value = document.get("csl")
    bibliography = project_root / str(bibliography_value) if bibliography_value else None
    publications_bibliography = project_root / str(publications_value) if publications_value else None
    csl = project_root / str(csl_value) if csl_value else None
    bibliography_title = str(document.get("bibliography_title", "СПИСОК ЛИТЕРАТУРЫ"))
    if bibliography is not None and not bibliography.is_file():
        raise ValueError(f"bibliography file does not exist: {bibliography}")
    if publications_bibliography is not None and not publications_bibliography.is_file():
        raise ValueError(f"publications bibliography file does not exist: {publications_bibliography}")
    if csl is not None and not csl.is_file():
        raise ValueError(f"CSL file does not exist: {csl}")
    return DocumentConfig(
        project_root,
        content_dir,
        root_note,
        output,
        assembled_markdown,
        bibliography,
        publications_bibliography,
        csl,
        bibliography_title,
    )
