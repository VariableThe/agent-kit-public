---
name: image-palette
description: Extract dominant colors as hex. Triggers: dominant colors, color palette, extract colors, theme colors.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pillow
---

# Image Palette

Local color sampling. Nothing leaves the machine.

## Setup

```bash
pip install pillow
```

## Commands

```bash
# Hex codes, most dominant first
python3 scripts/palette.py photo.jpg --colors 8

# With pixel counts as JSON
python3 scripts/palette.py photo.jpg --json
```

## Rules

- Median-cut quantization on a downscaled copy: fast and representative.
- More than ~12 colors adds noise, not insight.
