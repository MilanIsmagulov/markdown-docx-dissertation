from __future__ import annotations

import re
from pathlib import Path

import yaml

from .model import Heading, Link, Note, SourceLocation


WIKI_LINK_RE = re.compile(
    r"(?P<embed>!)?\[\[(?P<target>[^\]|#]+?)"
    r"(?:#(?P<heading>[^\]|]+?))?(?:\|(?P<alias>[^\]]+?))?\]\]"
)
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<text>.+?)\s*$")
INLINE_CODE_RE = re.compile(r"(?P<ticks>`+).*?(?P=ticks)")


class ParseError(ValueError):
    def __init__(self, path: Path, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path


def _split_front_matter(path: Path, text: str) -> tuple[dict, str, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, 1
    closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        raise ParseError(path, "front matter is not closed with '---'")
    try:
        metadata = yaml.safe_load("".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise ParseError(path, f"invalid YAML front matter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ParseError(path, "front matter must be a YAML mapping")
    return metadata, "".join(lines[closing + 1 :]), closing + 2


def parse_note(path: Path) -> Note:
    text = path.read_text(encoding="utf-8-sig")
    metadata, body, first_body_line = _split_front_matter(path, text)
    headings: list[Heading] = []
    links: list[Link] = []
    in_fence = False

    for offset, line in enumerate(body.splitlines(), first_body_line):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading_match = HEADING_RE.match(line)
        if heading_match:
            headings.append(
                Heading(len(heading_match["marks"]), heading_match["text"].strip(), SourceLocation(path, offset))
            )
        searchable_line = INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)
        for match in WIKI_LINK_RE.finditer(searchable_line):
            links.append(
                Link(
                    raw=match.group(0),
                    target=match["target"].strip(),
                    heading=match["heading"].strip() if match["heading"] else None,
                    alias=match["alias"].strip() if match["alias"] else None,
                    kind="transclusion" if match["embed"] else "link",
                    location=SourceLocation(path, offset, match.start() + 1),
                )
            )

    declared_title = metadata.get("title")
    title = str(declared_title) if declared_title else (headings[0].text if headings else path.stem)
    declared_id = metadata.get("id")
    return Note(
        path=path,
        note_id=str(declared_id).strip() if declared_id is not None else None,
        title=title,
        metadata=metadata,
        body=body,
        headings=headings,
        links=links,
    )
