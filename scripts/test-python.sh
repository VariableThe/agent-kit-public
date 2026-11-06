#!/usr/bin/env bash
# Runs every Python test that ships inside a skill. Each test file is executable
# on its own (`unittest.main()`), so no package __init__ files are required.
# Usage: bash scripts/test-python.sh
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

status=0
count=0
while IFS= read -r file; do
  count=$((count + 1))
  printf '== %s\n' "$file"
  python3 "$file" || status=1
done < <(find skills -type f \( -name 'test_*.py' -o -name '*_test.py' \) -path '*/test/*' | sort)

if [ "$count" -eq 0 ]; then
  echo "no python tests found" >&2
  exit 1
fi

if [ "$status" -eq 0 ]; then
  printf '%d python test file(s) passed\n' "$count"
fi
exit "$status"
