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
    return DocumentConfig(project_root, content_dir, root_note, output, assembled_markdown)

