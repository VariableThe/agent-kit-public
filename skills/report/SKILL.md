---
name: report
description: Generate structured reports (Markdown to DOCX) with a repeatable draft-review-render workflow. Triggers: write a report, generate a report, report from notes, turn notes into a document, build a DOCX, weekly report, status report.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: stdlib-only
  source: Adapted from JetBrains/skills doc/ (Apache-2.0)
---

# Report

Turn notes or a Markdown draft into a real report: skeleton first, write
second, render third, inspect the result before handing it over.

Standard library only — `python3 scripts/report.py --help` works with
zero setup. The .docx is built with `zipfile` + XML; no office engine,
no converter, no dependency.

## Purpose

- `outline` — a report skeleton to fill in.
- `build` — Markdown to a styled .docx.
- `inspect` — what actually landed in the file.
- `to-text` — the document as plain text.
- `validate` — is the package actually sound?

## Commands

| Command | Effect |
| --- | --- |
| `outline -o out.md [--title T] [--section NAME]...` | skeleton: Title + Summary, Background, Findings, Recommendations, Appendix (+ your sections) |
| `build <source.md> -o <out.docx> [--title T] [--author A]` | render the .docx |
| `inspect <file.docx>` | heading outline, paragraph/table counts, word count |
| `to-text <file.docx>` | plain text to stdout |
| `validate <file.docx>` | zip integrity, required parts, XML parses |

## Workflow

1. `report.py outline -o draft.md --title "Q3 Review"` — start from the
   skeleton; it is a real structure, not a template to delete.
2. Write the content in Markdown. Section order stays as the skeleton.
3. `report.py build draft.md -o report.docx` — render.
4. `report.py inspect report.docx` — check the outline reads correctly
   and the counts match what you wrote.
5. `report.py validate report.docx` — confirm the package is sound
   before delivering it.
6. `report.py to-text report.docx` — read the delivered text one more
   time; this is what the reader will see.

## Markdown subset

- ATX headings `#` to `######` → real Word `Heading1`–`Heading6`
  styles (outline-navigable, not bold text pretending to be a heading).
- Paragraphs, unordered lists (`-`, `*`), ordered lists (`1.`).
- Fenced code blocks (```` ```lang ````) → monospaced, shaded.
- GFM pipe tables → bordered Word tables; short rows are padded so the
  table stays rectangular.
- Blockquotes (`>`) and horizontal rules (`---`).
- Inline `**bold**`, `*italic*`, `` `code` ``, `[text](url)` (a real
  hyperlink with an external relationship).

Nested lists are flattened to one level; keep the source flat.

## Quality bar

- Every section exists because the report needs it — drop sections that
  say nothing; add sections the reader will ask for.
- Lead with the finding, not with the method. The summary must be
  readable on its own.
- Numbers carry the argument: no unfilled placeholders, no "TBD", no
  lorem ipsum in a delivered document.
- Tables earn their place. A two-column table of prose is a list.
- Read `to-text` output once before delivering. It catches the missing
  section and the half-written sentence.

## Attribution

Adapted from the `doc/` skill in [JetBrains/skills](https://github.com/JetBrains/skills)
(Apache License 2.0 — see `LICENSE`). What was kept: structure before
prose, the draft → render → inspect loop, and the rule that a delivered
document ships without placeholders. What changed: this is an
independent implementation (Markdown subset, own DOCX writer, own
tests), the upstream's editing-tool and PDF-conversion tooling was
dropped, and the skill is scoped to Markdown → DOCX only. The upstream
notice is preserved in `LICENSE`; no upstream code is copied.

## Rules

- Failure: one `Error: <message>` line on stderr, exit 1. argparse usage
  errors exit 2. Success prints one short summary line
  (`Wrote report.docx (42 paragraphs, 3 tables)`).
- All source text is XML-escaped; content can never break the package.
- Output is a real OOXML package: `[Content_Types].xml`, `_rels/.rels`,
  `word/document.xml`, `word/styles.xml`, `word/numbering.xml`,
  `word/_rels/document.xml.rels`, `docProps/core.xml`.
- A4 page, 1 inch margins, `w:sectPr` closes the body.
- Never overwrite a delivered .docx without being asked; build to a new
  path instead.