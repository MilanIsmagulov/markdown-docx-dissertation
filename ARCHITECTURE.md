# Architecture

This repository is a compiler, not a collection of Word automation scripts.

## Sources and responsibilities

- Markdown stores content and semantic markup.
- YAML stores document configuration, metadata, and presentation rules.
- BibTeX stores bibliography data.
- LaTeX is the source language for mathematics.
- DOCX is a generated artifact and never a source of truth.
- Obsidian is an optional editor. Project syntax is implemented by this compiler.

## Pipeline

```text
Markdown + YAML + BibTeX
          |
          v
 parser -> project index -> resolver -> validator -> document AST
                                                    |
                                                    v
                                      DOCX/OMML renderer (later)
```

The parser and resolver must not depend on `python-docx`. Rendering consumes the
validated internal model; it does not discover files or repair broken links.

## Identity and links

Filesystem paths are not semantic identities. A Markdown file may declare a
stable `id` in YAML front matter. Wiki links (`[[...]]`) and transclusions
(`![[...]]`) can target an ID, a note name, and optionally a heading. Duplicate
IDs and ambiguous note names are validation errors.

References such as equations use stable names (`eq:loss`), while display numbers
are assigned during document linearization. Moving content therefore cannot
invalidate references.

## Mathematics

The intended rendering path is LaTeX -> internal equation node -> MathML ->
OMML. SVG is only a fallback for unsupported expressions. Equations in DOCX
must remain editable whenever possible.

## Current milestone

The first milestone is `parser -> index -> resolver -> validator`, exposed as
`python build.py validate`. DOCX rendering starts only after this layer has
stable tests.

