#!/usr/bin/env bash
# Pre-release gate for a public repository.
#
# `check-secrets.sh` looks for credential-shaped strings. This script is the
# second half of the release gate: it looks for private *context* that a
# secret scanner misses (internal host names, tailnet addresses, personal home
# directories, personal mail addresses) and for credential files that were
# committed by accident.
#
# The built-in patterns are generic and safe to publish. Extra literal terms
# (internal host names, account names, private domains) belong in a local,
# gitignored file: scripts/private-context.local.txt, one term per line.
#
# Exit 0 = safe to publish. Exit 1 = something private is staged.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"

LOCAL_TERMS_FILE="scripts/private-context.local.txt"

# Generic private-context patterns. None of these encode a host name, an
# account name, or a domain, so this file stays publishable.
GENERIC_PATTERNS=(
  "/home/[a-z][a-z0-9_-]*/"                    # absolute personal home directory
  "\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b" # tailnet CGNAT address
  "\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b"        # RFC1918 address
  "(^|[[:space:]])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b" # RFC1918 address
)

# Personal mail addresses. Documentation examples use example.* and pass.
EMAIL_PATTERN="[A-Za-z0-9._%+-]+@(gmail|yandex|proton(mail)?|outlook|hotmail|icloud|zoho)\.[A-Za-z]{2,}"

# Files that must never be tracked.
FORBIDDEN_PATHS='(^|/)(service\.json|auth\.json|credentials\.env|credentials\.local\.env|\.env)$'
ALLOWED_ENV='(^|/)credentials\.example\.env$'

FOUND=0
REPORT=()

tracked_files() {
  git ls-files 2>/dev/null || true
}

echo "== tracked credential files =="
FILE_HITS=0
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  if [[ "$file" =~ $FORBIDDEN_PATHS ]] && ! [[ "$file" =~ $ALLOWED_ENV ]]; then
    REPORT+=("credential-shaped file is tracked: $file")
    FILE_HITS=1
    FOUND=1
  fi
done < <(tracked_files)
[[ $FILE_HITS -eq 0 ]] && echo "none"

# Local-only extra terms, if the maintainer keeps a list.
LOCAL_TERMS=()
if [[ -f "$LOCAL_TERMS_FILE" ]]; then
  while IFS= read -r term; do
    term="${term#"${term%%[![:space:]]*}"}"
    term="${term%"${term##*[![:space:]]}"}"
    [[ -n "$term" && "$term" != \#* ]] && LOCAL_TERMS+=("$term")
  done < "$LOCAL_TERMS_FILE"
fi
if [[ ${#LOCAL_TERMS[@]} -gt 0 ]]; then
  echo "== local-only terms (${#LOCAL_TERMS[@]}) =="
else
  echo "== local-only terms (none configured) =="
fi

echo "== private context =="
CONTEXT_FOUND=0
while IFS= read -r file; do
  [[ -f "$file" ]] || continue
  case "$file" in
    scripts/check-secrets.sh|scripts/check-public-release.sh) continue ;;
  esac
  for pattern in "${GENERIC_PATTERNS[@]}"; do
    matches=$(grep -n -E "$pattern" "$file" 2>/dev/null | grep -v REDACTED || true)
    if [[ -n "$matches" ]]; then
      REPORT+=("private context in $file:"$'\n'"$matches")
      CONTEXT_FOUND=1
      FOUND=1
    fi
  done
  for term in "${LOCAL_TERMS[@]}"; do
    matches=$(grep -n -F "$term" "$file" 2>/dev/null | grep -v REDACTED || true)
    if [[ -n "$matches" ]]; then
      REPORT+=("private term in $file:"$'\n'"$matches")
      CONTEXT_FOUND=1
      FOUND=1
    fi
  done
  # License texts carry upstream attribution by design; the license asks for
  # the author's contact to stay in the notice, so the mail check skips them.
  if [[ ! "$file" =~ (^|/)(LICENSE|COPYING)(\.[A-Za-z0-9]+)?$ ]]; then
    matches=$(grep -n -E "$EMAIL_PATTERN" "$file" 2>/dev/null || true)
    if [[ -n "$matches" ]]; then
      REPORT+=("personal email in $file:"$'\n'"$matches")
      CONTEXT_FOUND=1
      FOUND=1
    fi
  fi
done < <(tracked_files)
[[ $CONTEXT_FOUND -eq 0 ]] && echo "none"

echo "== credential patterns =="
if bash scripts/check-secrets.sh; then
  :
else
  REPORT+=("check-secrets.sh flagged a credential pattern")
  FOUND=1
fi

if [[ $FOUND -eq 1 ]]; then
  echo
  echo "Private data detected:"
  printf '%s\n' "${REPORT[@]}"
  echo
  echo "Release blocked"
  exit 1
fi

echo
echo "Public release check passed"
exit 0
