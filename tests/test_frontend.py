from pathlib import Path

import yaml

from builder.scaffold import scaffold

from builder.parser import parse_note
from builder.resolver import build_index
from builder.validator import validate_index
from builder.assembler import assemble_note
from builder.assets import process_assets
from builder.docx_renderer import _set_font
from builder.config import load_document_config
from docx import Document
from docx.oxml.ns import qn


def write_note(root: Path, relative: str, text: str) -> Path:
    path = root / "content" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_document_config_resolves_bibliography_and_csl(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "bibliography").mkdir()
    (tmp_path / "assets" / "csl").mkdir(parents=True)
    (tmp_path / "bibliography" / "library.bib").write_text("", encoding="utf-8")
    (tmp_path / "assets" / "csl" / "gost.csl").write_text("<style/>", encoding="utf-8")
    (tmp_path / "config" / "document.yaml").write_text(
        """document:
  bibliography: bibliography/library.bib
  csl: assets/csl/gost.csl
  bibliography_title: СПИСОК ИСТОЧНИКОВ
""",
        encoding="utf-8",
    )

    config = load_document_config(tmp_path)

    assert config.bibliography == tmp_path / "bibliography" / "library.bib"
    assert config.csl == tmp_path / "assets" / "csl" / "gost.csl"
    assert config.bibliography_title == "СПИСОК ИСТОЧНИКОВ"


def test_parser_reads_metadata_links_and_transclusions(tmp_path: Path) -> None:
    path = write_note(
        tmp_path,
        "root.md",
        "---\nid: document:root\ntitle: Root\n---\n# Intro\n[[eq:loss|formula]]\n![[Method#Details]]\n",
    )
    note = parse_note(path)
    assert note.note_id == "document:root"
    assert [link.kind for link in note.links] == ["link", "transclusion"]
    assert note.links[1].heading == "Details"


def test_parser_ignores_links_inside_code(tmp_path: Path) -> None:
    path = write_note(tmp_path, "root.md", "`[[inline]]`\n```md\n![[fenced]]\n```\n[[real]]\n")
    note = parse_note(path)
    assert [link.target for link in note.links] == ["real"]


def test_resolver_accepts_stable_id_and_heading(tmp_path: Path) -> None:
    root = write_note(tmp_path, "root.md", "# Root\n![[method:one#Details]]\n")
    write_note(tmp_path, "methods/renamed.md", "---\nid: method:one\n---\n# Method\n## Details\nText\n")
    index = build_index(tmp_path, tmp_path / "content")
    assert validate_index(index, root) == []


def test_validator_reports_missing_heading_and_target(tmp_path: Path) -> None:
    root = write_note(tmp_path, "root.md", "# Root\n[[Missing]]\n![[Existing#Absent]]\n")
    write_note(tmp_path, "Existing.md", "# Existing\n## Present\n")
    diagnostics = validate_index(build_index(tmp_path, tmp_path / "content"), root)
    assert {item.code for item in diagnostics} == {"E_LINK_MISSING", "E_HEADING_MISSING"}


def test_validator_reports_transclusion_cycle(tmp_path: Path) -> None:
    root = write_note(tmp_path, "A.md", "# A\n![[B]]\n")
    write_note(tmp_path, "B.md", "# B\n![[A]]\n")
    diagnostics = validate_index(build_index(tmp_path, tmp_path / "content"), root)
    assert "E_TRANSCLUSION_CYCLE" in {item.code for item in diagnostics}


def test_assembler_expands_equation_note_as_markdown_math(tmp_path: Path) -> None:
    root = write_note(tmp_path, "root.md", "# Root\n![[equation:loss]]\n")
    write_note(
        tmp_path,
        "equations/loss.md",
        "---\nid: equation:loss\ntype: equation\n---\n$$\nL(\\theta) = -\\sum_i y_i \\log p_i\n$$\n",
    )
    index = build_index(tmp_path, tmp_path / "content")
    root_note = next(note for note in index.notes if note.path == root)
    assembled = assemble_note(index, root_note)
    assert "![[" not in assembled
    assert "$$\nL(\\theta) = -\\sum_i y_i \\log p_i\n$$" in assembled


def test_word_heading_font_does_not_keep_theme_reference() -> None:
    document = Document()
    style = document.styles["Heading 1"]
    _set_font(style, "Times New Roman", "14pt", True)
    fonts = style.element.get_or_add_rPr().rFonts
    assert fonts.get(qn("w:ascii")) == "Times New Roman"
    assert fonts.get(qn("w:hAnsi")) == "Times New Roman"
    assert fonts.get(qn("w:eastAsia")) == "Times New Roman"
    assert fonts.get(qn("w:cs")) == "Times New Roman"
    assert fonts.get(qn("w:asciiTheme")) is None
    assert fonts.get(qn("w:hAnsiTheme")) is None


def test_asset_processor_numbers_csv_figure_equation_and_references(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "data.csv").write_text("Показатель,Значение\nТочность,0.93\n", encoding="utf-8")
    (assets / "scheme.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    markdown = """# Глава 1

См. {{ref:table:data}}, {{ref:figure:scheme}} и {{ref:equation:score}}.

```table
source: assets/data.csv
id: data
caption: Результаты
```

```figure
source: assets/scheme.svg
id: scheme
caption: Схема
```

```equation
id: score
latex: Q = A + B
```
"""
    result = process_assets(markdown, tmp_path)
    assert "таблица 1.1, рисунок 1.1 и (1.1)" in result
    assert "Таблица 1.1 – Результаты" in result
    assert "| Показатель | Значение |" in result
    assert "![Рисунок 1.1 – Схема]" in result
    assert "[[EQUATION:1.1]]" in result
    assert "$$\nQ = A + B\n$$" in result


def test_scaffold_creates_nested_obsidian_structure_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "metadata.yaml").write_text(
        yaml.safe_dump(
            {"structure": {"chapters": 2, "paragraphs_per_chapter": 2}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    existing = tmp_path / "content" / "01 Chapter" / "1.1. Авторское название.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("## 1.1. Авторское название\n\nАвторский текст.\n", encoding="utf-8")

    created, chapters = scaffold(tmp_path)
    assert chapters == 2
    assert created == 10
    root = (tmp_path / "content" / "root.md").read_text(encoding="utf-8")
    chapter = (tmp_path / "content" / "01 Chapter" / "Глава 1. Тест.md").read_text(encoding="utf-8")
    assert "![[01 Chapter/Глава 1. Тест]]" in root
    assert "![[01 Chapter/Преамбула главы 1]]" in chapter
    assert "![[01 Chapter/1.1. Авторское название]]" in chapter
    assert "![[01 Chapter/Выводы по главе 1]]" in chapter
    chapter_two = (tmp_path / "content" / "02 Chapter" / "Глава 2. Тест.md").read_text(encoding="utf-8")
    assert "![[02 Chapter/2.1. Параграф]]" in chapter_two
    assert "![[02 Chapter/2.2. Параграф]]" in chapter_two
    paragraph_two = tmp_path / "content" / "02 Chapter" / "2.2. Параграф.md"
    assert "id: paragraph:2.2" in paragraph_two.read_text(encoding="utf-8")

    paragraph = existing
    paragraph.write_text(paragraph.read_text(encoding="utf-8") + "\nАвторский текст.\n", encoding="utf-8")
    created_again, _ = scaffold(tmp_path)
    assert created_again == 0
    assert "Авторский текст." in paragraph.read_text(encoding="utf-8")
