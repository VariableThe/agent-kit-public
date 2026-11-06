#!/usr/bin/env bash
# Guards the split between the tested Linux release and the Windows changes
# held back for testing: every Windows executable path in the tree must be
# byte-identical to the base branch, and this change set must not add Windows
# behaviour to repo code.
#
# The base is computed, not hard-coded: the merge base of HEAD and origin/main
# (or a base ref you pass in), so the check stays meaningful on later branches
# and trivially passes on the merged result.
# Run: bash test/windows-split.test.sh [base-ref]   (or npm run test:split)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT" || exit 1

BASE="${1:-}"
if [ -z "$BASE" ]; then
  for ref in origin/main main; do
    if git rev-parse --verify --quiet "$ref" >/dev/null; then
      BASE="$(git merge-base HEAD "$ref" 2>/dev/null)" && [ -n "$BASE" ] && break
    fi
  done
fi
if [ -z "$BASE" ] && git rev-parse --verify --quiet HEAD^ >/dev/null; then
  BASE="$(git rev-parse HEAD^)"
fi
if [ -z "$BASE" ]; then
  echo "cannot determine a base commit; pass one explicitly" >&2
  exit 1
fi
echo "base: $(git rev-parse --short "$BASE")"

WINDOWS_RE='\.(ps1|psm1|psd1|bat|cmd|exe|dll|msi|vbs|csproj|sln)$'
fail=0

# 1. No Windows executable path may appear as added, modified or deleted.
changed="$(git diff --name-only "$BASE"...HEAD | grep -Ei "$WINDOWS_RE" || true)"
if [ -n "$changed" ]; then
  echo "FAIL - this change set touches Windows executable paths:" >&2
  printf '  %s\n' $changed >&2
  fail=1
else
  echo "ok   - no Windows executable path changed"
fi

# 2. Every Windows executable path present at the base must still be there,
#    byte for byte, so nothing was quietly deleted or rewritten.
count=0
mismatch=0
while IFS= read -r path; do
  [ -n "$path" ] || continue
  count=$((count + 1))
  if [ ! -f "$path" ]; then
    echo "FAIL - $path is missing (Windows file removed)" >&2
    mismatch=$((mismatch + 1))
  elif ! git show "$BASE:$path" | cmp -s - "$path"; then
    echo "FAIL - $path differs from the base version" >&2
    mismatch=$((mismatch + 1))
  fi
done < <(git ls-tree -r --name-only "$BASE" | grep -Ei "$WINDOWS_RE" || true)
if [ "$count" -eq 0 ]; then
  echo "note - the base carries no Windows executable paths"
elif [ "$mismatch" -eq 0 ]; then
  echo "ok   - $count Windows executable path(s) identical to base"
else
  echo "FAIL - $mismatch of $count Windows executable path(s) differ from base" >&2
  fail=1
fi

# 3. Repo code (not vendored skills, not prose) gains no Windows-only branch:
#    those changes are exactly what a Windows follow-up PR has to carry. The
#    checker itself is excluded because it necessarily spells out the patterns.
deltas="$(git diff -U0 "$BASE"...HEAD -- . \
  ':(exclude)skills' ':(exclude)docs' ':(exclude)README.md' \
  ':(exclude)test/windows-split.test.sh' |
  grep -E '^\+' | grep -vE '^\+\+\+' |
  grep -Ei 'PlatformID::|OSVersion|onWindows|process\.platform|win32|powershell|\.ps1' || true)"
if [ -n "$deltas" ]; then
  echo "FAIL - repo code adds Windows-specific behaviour:" >&2
  printf '  %s\n' "$deltas" >&2
  fail=1
else
  echo "ok   - repo code adds no Windows-specific behaviour"
fi

if [ "$fail" -eq 0 ]; then
  echo "all checks passed"
else
  echo "checks failed"
fi
exit "$fail"
