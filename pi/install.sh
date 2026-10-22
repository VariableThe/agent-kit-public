#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project python "$bundle_dir/install.py" install "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 "$bundle_dir/install.py" install "$@"
fi
printf '%s\n' 'Install uv or Python 3.10+ first.' >&2
exit 1
