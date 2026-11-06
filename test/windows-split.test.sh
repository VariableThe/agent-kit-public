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

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

# Canonicalise a ref to a full commit id, or fail. Anything that is not a real
# commit (a missing ref, an option, a tree or blob id) must never reach the
# comparisons below: git would then print nothing and the check would pass on
# no evidence.
canonical_base() {
  local ref="$1" commit
  case "$ref" in
    "" | -*) return 1 ;;
  esac
  commit="$(git rev-parse --verify --quiet --end-of-options "${ref}^{commit}")" ||
    return 1
  [ -n "$commit" ] || return 1
  printf '%s\n' "$commit"
}

BASE=""
if [ "$#" -gt 0 ]; then
  BASE="$(canonical_base "$1")" || die "not a commit: $1"
else
  for ref in origin/main main HEAD^; do
    candidate="$(canonical_base "$ref")" || continue
    BASE="$(git merge-base HEAD "$candidate")" ||
      die "git merge-base failed for $ref"
    [ -n "$BASE" ] || die "no merge base with $ref"
    break
  done
  [ -n "$BASE" ] || die "cannot determine a base commit; pass one explicitly"
fi
echo "base: $(git rev-parse --short "$BASE")"

# Windows executable paths. .NET project files (`.csproj`, `.sln`) are left out
# on purpose: they are portable project metadata, not Windows executables.
WINDOWS_RE='\.(ps1|psm1|psd1|bat|cmd|exe|dll|msi|vbs)$'
fail=0

# The patterns must not swallow portable .NET project files.
if printf '%s\n' App.csproj App.sln | grep -Eic "$WINDOWS_RE" | grep -qv '^0$'; then
  echo "FAIL - WINDOWS_RE treats .NET project files as Windows executables" >&2
  fail=1
else
  echo "ok   - .csproj/.sln are not classified as Windows executables"
fi

# 1. No Windows executable path may appear as added, modified or deleted.
if ! diff_out="$(git diff --name-only "$BASE"...HEAD)"; then
  die "git diff failed for base $BASE"
fi
changed="$(printf '%s\n' "$diff_out" | grep -Ei "$WINDOWS_RE" || true)"
if [ -n "$changed" ]; then
  echo "FAIL - this change set touches Windows executable paths:" >&2
  printf '%s\n' "$changed" | sed 's/^/  /' >&2
  fail=1
else
  echo "ok   - no Windows executable path changed"
fi

# 2. Every Windows executable path present at the base must still be there,
#    byte for byte, so nothing was quietly deleted or rewritten.
if ! base_files="$(git ls-tree -r --name-only "$BASE")"; then
  die "git ls-tree failed for base $BASE"
fi
count=0
mismatch=0
windows_paths="$(printf '%s\n' "$base_files" | grep -Ei "$WINDOWS_RE" || true)"
while IFS= read -r path; do
  [ -n "$path" ] || continue
  count=$((count + 1))
  if ! git cat-file -e "$BASE:$path" 2>/dev/null; then
    echo "FAIL - $path is missing from base" >&2
    mismatch=$((mismatch + 1))
  elif [ ! -f "$path" ]; then
    echo "FAIL - $path is missing (Windows file removed)" >&2
    mismatch=$((mismatch + 1))
  elif ! git show "$BASE:$path" | cmp -s - "$path"; then
    echo "FAIL - $path differs from the base version" >&2
    mismatch=$((mismatch + 1))
  fi
done <<< "$windows_paths"
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
if ! code_out="$(git diff -U0 "$BASE"...HEAD -- . \
  ':(exclude)skills' ':(exclude)docs' ':(exclude)README.md' \
  ':(exclude)test/windows-split.test.sh')"; then
  die "git diff failed for base $BASE"
fi
deltas="$(printf '%s\n' "$code_out" |
  grep -E '^\+' | grep -vE '^\+\+\+' |
  grep -Ei 'PlatformID::|OSVersion|onWindows|process\.platform|win32|powershell|\.ps1' || true)"
if [ -n "$deltas" ]; then
  echo "FAIL - repo code adds Windows-specific behaviour:" >&2
  printf '%s\n' "$deltas" | sed 's/^/  /' >&2
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
