from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


BEGIN = "<!-- scaffold:begin -->"
END = "<!-- scaffold:end -->"
TRANSCLUSION = re.compile(r"^\s*!\[\[[^]]+]]\s*$")


@dataclass(frozen=True)
class Structure:
    chapters: int = 4
    paragraphs_per_chapter: int = 4
    chapter_title_template: str = "Глава {chapter}. Тест"
    paragraph_title_template: str = "{chapter}.{paragraph}. Параграф"
    preamble_title_template: str = "Преамбула главы {chapter}"
    conclusions_title_template: str = "Выводы по главе {chapter}"


def load_structure(project_root: Path) -> Structure:
    path = project_root / "config" / "metadata.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    raw = metadata.get("structure", {})
    if not isinstance(raw, dict):
        raise ValueError("metadata.structure must be a mapping")
    values = {field: raw[field] for field in Structure.__dataclass_fields__ if field in raw}
    structure = Structure(**values)
    if structure.chapters < 1 or structure.paragraphs_per_chapter < 1:
        raise ValueError("structure counts must be positive integers")
    return structure


def _front_matter(note_id: str, note_type: str, title: str) -> str:
    return f"---\nid: {note_id}\ntype: {note_type}\ntitle: {title}\n---\n\n"


def _paragraph_front_matter(chapter: int, paragraph: int, title: str) -> str:
    return (
        f"---\nid: paragraph:{chapter}.{paragraph}\ntype: paragraph\n"
        f"chapter: {chapter}\nparagraph: {paragraph}\nnumber: {chapter}.{paragraph}\n"
        f"title: {title}\n---\n\n"
    )


def _write_new(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _replace_link_block(path: Path, links: list[str], *, after_heading: bool) -> None:
    text = path.read_text(encoding="utf-8-sig")
    block = BEGIN + "\n" + "\n".join(f"![[{link}]]" for link in links) + "\n" + END
    if BEGIN in text and END in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), block, text, flags=re.DOTALL)
    else:
        lines = [line for line in text.splitlines() if not TRANSCLUSION.match(line)]
        insertion = len(lines)
        if after_heading:
            insertion = next((i + 1 for i, line in enumerate(lines) if line.startswith("# ")), insertion)
        else:
            closing = [i for i, line in enumerate(lines) if line.strip() == "---"]
            if len(closing) >= 2:
                insertion = closing[1] + 1
        lines[insertion:insertion] = ["", block, ""]
        text = "\n".join(lines).strip() + "\n"
    path.write_text(text, encoding="utf-8")


def scaffold(project_root: Path) -> tuple[int, int]:
    structure = load_structure(project_root)
    content = project_root / "content"
    root = content / "root.md"
    root_links: list[str] = []
    created = 0

    for chapter in range(1, structure.chapters + 1):
        chapter_title = structure.chapter_title_template.format(chapter=chapter)
        chapter_dir = content / f"{chapter:02d} Chapter"
        chapter_path = chapter_dir / f"{chapter_title}.md"
        root_links.append(f"{chapter:02d} Chapter/{chapter_title}")
        created += _write_new(
            chapter_path,
            _front_matter(f"chapter:{chapter}", "chapter", chapter_title) + f"# {chapter_title}\n",
        )

        preamble_title = structure.preamble_title_template.format(chapter=chapter)
        preamble_rel = f"{chapter:02d} Chapter/{preamble_title}"
        created += _write_new(
            content / f"{preamble_rel}.md",
            _front_matter(f"chapter:{chapter}:preamble", "chapter-preamble", preamble_title)
            + "В преамбуле кратко раскрываются цель главы, рассматриваемые задачи и связь "
            + "с предыдущими результатами исследования. Здесь размещается вводный текст главы.\n",
        )

        chapter_links = [preamble_rel]
        for paragraph in range(1, structure.paragraphs_per_chapter + 1):
            title = structure.paragraph_title_template.format(chapter=chapter, paragraph=paragraph)
            existing = [path for path in chapter_dir.glob(f"{chapter}.{paragraph}.*.md") if path.stem != title]
            selected_title = existing[0].stem if existing else title
            rel = f"{chapter:02d} Chapter/{selected_title}"
            chapter_links.append(rel)
            created += _write_new(
                content / f"{rel}.md",
                _paragraph_front_matter(chapter, paragraph, selected_title)
                + f"## {selected_title}\n\n"
                + "В данном параграфе излагаются основные положения исследования, приводятся "
                + "необходимые определения, аргументация и промежуточные результаты.\n",
            )

        conclusions_title = structure.conclusions_title_template.format(chapter=chapter)
        conclusions_rel = f"{chapter:02d} Chapter/{conclusions_title}"
        chapter_links.append(conclusions_rel)
        created += _write_new(
            content / f"{conclusions_rel}.md",
            _front_matter(f"chapter:{chapter}:conclusions", "chapter-conclusions", conclusions_title)
            + f"## {conclusions_title}\n\n"
            + "В главе получены основные результаты, сформулированы промежуточные выводы и "
            + "обозначена их связь с последующими этапами исследования.\n",
        )
        _replace_link_block(chapter_path, chapter_links, after_heading=True)

    if not root.exists():
        _write_new(root, _front_matter("document:root", "document", "Диссертация"))
        created += 1
    _replace_link_block(root, root_links, after_heading=False)
    return created, structure.chapters
