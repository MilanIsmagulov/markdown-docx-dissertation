# Markdown -> DOCX dissertation compiler

The project currently implements the validation front-end of a graph-aware
Markdown compiler.

```powershell
python -m pip install -e ".[dev]"
python build.py scaffold
python build.py validate
python build.py assemble
python build.py build
pytest
```

## Генерация структуры диссертации

Параметры структуры находятся в секции `structure` файла
`config/metadata.yaml`. По умолчанию создаются четыре главы и по четыре
параграфа в каждой. Команда `python build.py scaffold` создаёт:

- общий `content/root.md` со ссылками на файлы глав;
- файл каждой главы со ссылками на её преамбулу, параграфы и выводы;
- отдельную преамбулу с текстом сразу под заголовком главы;
- отдельные Markdown-файлы параграфов;
- отдельный файл «Выводы по главе».
- каталог `00 Front Matter` с файлом `Введение.md` и отдельными смысловыми
  блоками введения;
- отдельный файл `90 Back Matter/Заключение.md`.

Команду можно запускать повторно. Генератор обновляет только блоки ссылок между
файлами и создаёт отсутствующие заметки; существующий текст заметок не
перезаписывается. Все ссылки имеют нативный для Obsidian вид `![[...]]`.

The entry document is configured in `config/document.yaml`. Markdown notes may
use YAML front matter, `[[wiki links]]`, and `![[transclusions#Heading]]`.

## Obsidian and equations

Open `content/` as the Obsidian vault. Its local `.obsidian/app.json` selects
Wikilinks and **Absolute path in vault**, so links such as
`[[01 Chapter/1.2. Параграф]]` are always rooted at `content/`
and do not depend on the current note folder. The `_templates/` directory
contains templates for a paragraph, chapter preamble, and chapter conclusions.
Enable the core **Templates** plugin and insert the appropriate template after
creating a note. The built-in plugin can substitute `{{title}}`, but the numeric
part of `id` must be entered manually; `python build.py scaffold` remains the
fully automatic way to create a numbered structure.

The compiler syntax deliberately remains valid Obsidian Markdown. An equation
note can contain ordinary MathJax syntax:

```markdown
---
id: equation:loss
type: equation
---

$$
L(\theta) = -\sum_i y_i \log p_i
$$
```

Save it as `content/equations/loss.md` and embed it with
`![[equations/loss]]`. Obsidian resolves wiki links by note path/name (not by
the YAML `id`) and renders the embedded formula. The stable `id` remains
available to the compiler for semantic references. Then `python build.py
assemble` writes the expanded formula to `build/assembled.md`.
The future DOCX backend will render the same semantic equation as OMML.

## Numbered tables, figures, and equations

Markdown does not natively render an XLSX workbook. Keep source data in
`content/assets/tables/` as CSV, TSV, or XLSX and insert it with a fenced
directive. XLSX directives may select a worksheet with `sheet`:

````markdown
```table
source: assets/tables/results.xlsx
sheet: Experiment
id: results
caption: Results of the experiment
```
````

Figures use PNG or JPEG assets from `content/assets/images/`:

````markdown
```figure
source: assets/images/pipeline.png
id: pipeline
caption: Multimodal processing pipeline
width: 150mm
```
````

Numbered editable equations use LaTeX source:

````markdown
```equation
id: quality
latex: Q = \alpha A + \beta C
```
````

Objects are numbered independently within each chapter. Use
`{{ref:equation:quality}}` for a complete equation reference such as `(1.1)`.
For Russian grammatical cases, insert only the number and write the surrounding
phrase explicitly: `на рисунке {{number:figure:pipeline}}` or
`в таблице {{number:table:results}}`.

## Bibliography

Bibliographic records are stored in `bibliography/bibliography.bib`. Cite them
from any Markdown note with Pandoc citation keys:

```markdown
Метод описан в работе [@vaswani2017attention].
Сравнение подходов приведено в нескольких источниках
[@baltrusaitis2019multimodal; @radford2022whisper].
```

The build runs Pandoc Citeproc with the numeric GOST CSL style configured in
`config/document.yaml`. Only cited records are included in the automatically
generated `СПИСОК ЛИТЕРАТУРЫ` section. Keep BibTeX keys stable when editing or
exporting the library from Zotero. Obsidian's Citations plugin can use the same
`.bib` file for searching and inserting keys.

## PDF appendices

Appendices are assembled through `content/99 Appendices/Приложения.md`. A PDF
kept under `content/assets/appendices/` can be embedded with the same syntax
that Obsidian uses for its native PDF viewer:

```markdown
![[assets/appendices/implementation-act.pdf]]
```

During DOCX assembly, Poppler renders every PDF page at 200 DPI. Pages are
inserted at 160 mm width, and every page after the first starts on a new Word
page. To insert only one page, use `![[assets/appendices/file.pdf#page=2]]`.
The validator reports missing PDF assets before the build starts.
