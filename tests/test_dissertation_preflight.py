from pathlib import Path

from builder.cli import _document_validation_mode, main
from builder.model import Diagnostic, SourceLocation
from builder.resolver import build_index
from builder.validator import validate_index


def _note(root: Path, relative: str, note_id: str, note_type: str, body: str = "Текст.", **metadata) -> Path:
    path = root / "content" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{key}: {value}\n" for key, value in metadata.items())
    path.write_text(
        f"---\nid: {note_id}\ntype: {note_type}\ntitle: {path.stem}\n{extra}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _project(root: Path, *, placeholder: bool = False) -> tuple[Path, Path]:
    config = root / "config"
    config.mkdir()
    (config / "document.yaml").write_text(
        "document:\n  content: content\n  root: content/root.md\nvalidation:\n  mode: draft\n",
        encoding="utf-8",
    )
    intro = _note(root, "intro.md", "section:introduction", "structural-section")
    preamble = _note(root, "chapter/preamble.md", "chapter:1:preamble", "chapter-preamble")
    paragraph_text = "[указать результат]" if placeholder else "Основной текст параграфа."
    paragraph = _note(root, "chapter/paragraph.md", "paragraph:1.1", "paragraph", paragraph_text)
    conclusions = _note(root, "chapter/conclusions.md", "chapter:1:conclusions", "chapter-conclusions")
    chapter = _note(
        root, "chapter/chapter.md", "chapter:1", "chapter",
        "\n".join(f"![[{path.relative_to(root / 'content').with_suffix('').as_posix()}]]" for path in (preamble, paragraph, conclusions)),
    )
    conclusion = _note(root, "conclusion.md", "section:conclusion", "structural-section")
    references = _note(root, "references.md", "section:references", "bibliography", "{{bibliography}}")
    appendices = _note(root, "appendices.md", "section:appendices", "appendices", "Текст приложений.")
    root_note = _note(
        root, "root.md", "document:root", "document",
        "\n".join(f"![[{path.relative_to(root / 'content').with_suffix('').as_posix()}]]" for path in (
            intro, chapter, conclusion, references, appendices,
        )),
    )
    return root / "content", root_note


def _codes(root: Path, root_note: Path, mode: str) -> list[str]:
    index = build_index(root, root / "content")
    return [item.code for item in validate_index(index, root_note, validation_mode=mode)]


def test_valid_dissertation_structure_passes_preflight(tmp_path: Path) -> None:
    _, root_note = _project(tmp_path)

    codes = _codes(tmp_path, root_note, "final")

    assert not [code for code in codes if code.startswith(("E_STRUCTURE", "E_CHAPTER", "E_PARAGRAPH"))]


def test_placeholder_is_warning_in_draft_and_error_in_final(tmp_path: Path) -> None:
    _, root_note = _project(tmp_path, placeholder=True)

    assert "W_PLACEHOLDER" in _codes(tmp_path, root_note, "draft")
    assert "E_PLACEHOLDER" in _codes(tmp_path, root_note, "final")


def test_empty_leaf_is_warning_in_draft_and_error_in_final(tmp_path: Path) -> None:
    _, root_note = _project(tmp_path)
    paragraph = tmp_path / "content" / "chapter" / "paragraph.md"
    text = paragraph.read_text(encoding="utf-8")
    paragraph.write_text(text.replace("Основной текст параграфа.", "<!-- пусто -->"), encoding="utf-8")

    assert "W_NOTE_EMPTY" in _codes(tmp_path, root_note, "draft")
    assert "E_NOTE_EMPTY" in _codes(tmp_path, root_note, "final")


def test_structure_and_paragraph_order_are_checked(tmp_path: Path) -> None:
    _, root_note = _project(tmp_path)
    root_text = root_note.read_text(encoding="utf-8")
    root_text = root_text.replace("![[conclusion]]\n![[references]]", "![[references]]\n![[conclusion]]")
    root_note.write_text(root_text, encoding="utf-8")
    paragraph = tmp_path / "content" / "chapter" / "paragraph.md"
    paragraph.write_text(paragraph.read_text(encoding="utf-8").replace("paragraph:1.1", "paragraph:1.2"), encoding="utf-8")

    codes = _codes(tmp_path, root_note, "final")

    assert "E_STRUCTURE_ORDER" in codes
    assert "E_PARAGRAPH_ORDER" in codes


def test_unknown_metadata_and_unused_asset_are_reported(tmp_path: Path) -> None:
    _, root_note = _project(tmp_path)
    paragraph = tmp_path / "content" / "chapter" / "paragraph.md"
    paragraph.write_text(paragraph.read_text(encoding="utf-8").replace("type: paragraph", "type: paragraph\ntyep: typo"), encoding="utf-8")
    asset = tmp_path / "content" / "assets" / "unused.png"
    asset.parent.mkdir()
    asset.write_bytes(b"unused")

    diagnostics = validate_index(build_index(tmp_path, tmp_path / "content"), root_note, validation_mode="draft")

    assert "E_METADATA_UNKNOWN" in [item.code for item in diagnostics]
    assert "W_ASSET_UNUSED" in [item.code for item in diagnostics]
    assert next(item for item in diagnostics if item.code == "E_METADATA_UNKNOWN").hint


def test_diagnostic_format_includes_actionable_hint(tmp_path: Path) -> None:
    diagnostic = Diagnostic("E_TEST", "problem", SourceLocation(tmp_path / "note.md", 7), hint="Fix it.")

    assert diagnostic.format(tmp_path) == "note.md:7:1: ERROR E_TEST: problem Hint: Fix it."


def test_document_mode_comes_from_yaml_and_cli_override(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "document.yaml").write_text("validation:\n  mode: final\n", encoding="utf-8")

    assert _document_validation_mode(tmp_path) == "final"
    assert _document_validation_mode(tmp_path, "draft") == "draft"


def test_validate_cli_accepts_mode(tmp_path: Path, capsys) -> None:
    _project(tmp_path, placeholder=True)

    exit_code = main(["--project", str(tmp_path), "validate", "--mode", "final"])

    assert exit_code == 1
    assert "E_PLACEHOLDER" in capsys.readouterr().out
