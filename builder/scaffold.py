from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


BEGIN = "<!-- scaffold:begin -->"
END = "<!-- scaffold:end -->"
TRANSCLUSION = re.compile(r"^\s*!\[\[[^]]+]]\s*$")

INTRODUCTION_PARTS = (
    ("relevance", "Актуальность темы"),
    ("development", "Степень разработанности темы"),
    ("goal-tasks", "Цель и задачи исследования"),
    ("object-subject", "Объект и предмет исследования"),
    ("methods", "Методы исследования"),
    ("novelty", "Научная новизна"),
    ("significance", "Теоретическая и практическая значимость"),
    ("defense", "Положения, выносимые на защиту"),
    ("validity", "Степень достоверности результатов"),
    ("approbation", "Апробация результатов"),
    ("publications", "Публикации автора"),
    ("structure", "Структура и объём диссертации"),
)
APPENDIX_LETTERS = tuple(letter for letter in "АБВГДЕЖИКЛМНПРСТУФХЦШЩЭЮЯ")


@dataclass(frozen=True)
class Structure:
    chapters: int = 4
    paragraphs_per_chapter: int = 4
    appendices: int = 1
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
    if structure.chapters < 1 or structure.paragraphs_per_chapter < 1 or structure.appendices < 0:
        raise ValueError("structure counts must be positive integers")
    if structure.appendices > len(APPENDIX_LETTERS):
        raise ValueError(f"structure.appendices cannot exceed {len(APPENDIX_LETTERS)}")
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
    root_links: list[str] = ["00 Front Matter/Введение", "00 Front Matter/Списки"]
    created = 0

    introduction_dir = content / "00 Front Matter"
    introduction_path = introduction_dir / "Введение.md"
    created += _write_new(
        introduction_path,
        _front_matter("section:introduction", "structural-section", "Введение") + "# ВВЕДЕНИЕ\n",
    )
    introduction_links: list[str] = []
    for note_id, title in INTRODUCTION_PARTS:
        relative = f"00 Front Matter/{title}"
        introduction_links.append(relative)
        created += _write_new(
            content / f"{relative}.md",
            _front_matter(f"introduction:{note_id}", "introduction-part", title)
            + f"**{title}.** Здесь размещается текст соответствующего структурного элемента введения.\n",
        )
    _replace_link_block(introduction_path, introduction_links, after_heading=True)

    lists_rel = "00 Front Matter/Списки"
    created += _write_new(
        content / f"{lists_rel}.md",
        _front_matter("section:lists", "structural-section", "Списки")
        + "# СПИСОК РИСУНКОВ\n\n{{list:figures}}\n\n"
        + "# СПИСОК ТАБЛИЦ\n\n{{list:tables}}\n\n"
        + "# СПИСОК СОКРАЩЕНИЙ\n\n{{list:abbreviations}}\n\n"
        + "# СПИСОК ОБОЗНАЧЕНИЙ\n\n{{list:symbols}}\n\n"
        + "# СЛОВАРЬ ТЕРМИНОВ\n\n{{list:glossary}}\n",
    )

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

    conclusion_rel = "90 Back Matter/Заключение"
    root_links.append(conclusion_rel)
    created += _write_new(
        content / f"{conclusion_rel}.md",
        _front_matter("section:conclusion", "structural-section", "Заключение")
        + "# ЗАКЛЮЧЕНИЕ\n\n"
        + "В заключении обобщаются результаты исследования, формулируются основные выводы, "
        + "рекомендации и направления дальнейшей работы.\n",
    )

    references_rel = "90 Back Matter/Список литературы"
    root_links.append(references_rel)
    created += _write_new(
        content / f"{references_rel}.md",
        _front_matter("section:references", "bibliography", "Список литературы")
        + "# СПИСОК ЛИТЕРАТУРЫ\n\n::: {#refs}\n:::\n",
    )

    appendices_rel = "99 Appendices/Приложения"
    root_links.append(appendices_rel)
    appendices_path = content / f"{appendices_rel}.md"
    created += _write_new(
        appendices_path,
        _front_matter("section:appendices", "appendices", "Приложения"),
    )
    appendix_links: list[str] = []
    for index in range(structure.appendices):
        label = APPENDIX_LETTERS[index]
        title = f"Приложение {label}"
        relative = f"99 Appendices/{title}"
        appendix_links.append(relative)
        created += _write_new(
            content / f"{relative}.md",
            _front_matter(f"appendix:{label.casefold()}", "appendix", title)
            + f"# ПРИЛОЖЕНИЕ {label}\n\n"
            + "## Название приложения\n\n"
            + "Здесь размещается текст приложения или встраивание PDF-документа.\n",
        )
    _replace_link_block(appendices_path, appendix_links, after_heading=False)

    if not root.exists():
        _write_new(root, _front_matter("document:root", "document", "Диссертация"))
        created += 1
    _replace_link_block(root, root_links, after_heading=False)
    return created, structure.chapters
