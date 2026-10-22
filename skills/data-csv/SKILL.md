---
name: data-csv
description: Convert XML/plist to CSV locally. Triggers: XML to CSV, plist to CSV, flatten catalog, RSS to spreadsheet.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: stdlib-only
---

# Data CSV

Local XML flattening. Nothing leaves the machine. No dependencies.

## Setup

None. Standard library only (`plistlib`, `xml.etree`, `csv`).

## Commands

```bash
# Apple plist (e.g. music library Tracks) or standard XML catalog
python3 scripts/data_csv.py Library.xml -o ./csv/
python3 scripts/data_csv.py feed.xml
```

## Rules

- Plist dict-of-dicts (like `Tracks`) becomes one CSV per record group.
- Standard XML groups repeating child tags; leaf elements and attributes
  become columns. One CSV per tag when several record types mix.
- Fails loudly on malformed XML instead of guessing.
