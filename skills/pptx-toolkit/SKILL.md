---
name: pptx-toolkit
description: Merge/inspect/export PPTX decks. Triggers: merge PPTX, PPTX to PDF, slide count, deck assembly.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pymupdf, pillow
---

# PPTX Toolkit

Local PowerPoint deck assembly and slide-content export. Nothing leaves the machine.

`merge` and `info` are stdlib-only (`zipfile`). `to-pdf` needs `pymupdf`.

## Setup

```bash
pip install pymupdf pillow
```

## Commands

All commands take explicit output paths (`-o`) and never modify inputs.

```bash
# Combine decks in order (each slide keeps its own master/theme)
python3 scripts/pptx.py merge a.pptx b.pptx -o combined.pptx

# Slide count and slide size
python3 scripts/pptx.py info deck.pptx

# Handout-style PDF: slide text, images and tables, one page per slide
python3 scripts/pptx.py to-pdf deck.pptx -o deck.pdf
```

## Rules

- Verify inputs exist before running; the script exits non-zero otherwise.
- Confirm slide counts after merge (`Slides:` line in output).
- Merging preserves formatting by carrying each slide's layout/master/theme chain.
- `to-pdf` is a content export, not a pixel-faithful render; unsupported images are reported, never fatal.
- For PDF-to-PDF work (merge, split, rotate), use `pdf-toolkit`.
