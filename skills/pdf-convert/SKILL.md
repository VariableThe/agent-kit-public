---
name: pdf-convert
description: Convert images and PDF pages. Triggers: JPG to PDF, PDF to PNG, rasterize PDF.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pymupdf, pillow
---

# PDF Convert

Local raster and assembly conversion. Nothing leaves the machine.

## Setup

```bash
pip install pymupdf pillow
```

## Commands

```bash
# Images (JPG/PNG) into one PDF, fit on A4 pages (or --fit Letter, Legal, native)
python3 scripts/convert.py images-to-pdf scan1.png scan2.jpg -o doc.pdf

# All pages to PNG at 200dpi (default); --pages 1-3 and --dpi 300 supported
python3 scripts/convert.py pdf-to-png in.pdf -o ./pages/
```

## Rules

- Output directory is created if missing.
- DPI controls output resolution: 150 for previews, 300 for print.
- For structural edits (merge, split, rotate), use `pdf-toolkit`.
