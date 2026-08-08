from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: Path
    line: int
    column: int = 1


@dataclass(frozen=True, slots=True)
class Link:
    raw: str
    target: str
    heading: str | None
    alias: str | None
    kind: Literal["link", "transclusion"]
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str
    location: SourceLocation


@dataclass(slots=True)
class Note:
    path: Path
    note_id: str | None
    title: str
    metadata: dict[str, Any]
    body: str
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    location: SourceLocation | None = None
    severity: Literal["error", "warning"] = "error"

    def format(self, root: Path | None = None) -> str:
        prefix = self.severity.upper()
        if self.location is None:
            return f"{prefix} {self.code}: {self.message}"
        path = self.location.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return f"{path}:{self.location.line}:{self.location.column}: {prefix} {self.code}: {self.message}"

