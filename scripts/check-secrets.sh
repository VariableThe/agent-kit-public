#!/usr/bin/env bash
set -eo pipefail

PATTERNS=(
  "sk-[A-Za-z0-9_-]{20,}"
  "ghp_[A-Za-z0-9]{36}"
  "github_pat_[A-Za-z0-9_-]{220}"
  "AIza[A-Za-z0-9_-]+"
  "xai-[A-Za-z0-9_-]+"
  "gsk_[A-Za-z0-9_-]+"
  "Bearer [A-Za-z0-9_.-]+"
)

FOUND=0

while IFS= read -r file; do
  [[ -f "$file" ]] || continue
  case "$file" in
    scripts/check-secrets.sh) continue ;;
  esac
  for pattern in "${PATTERNS[@]}"; do
    matches=$(grep -n -E "$pattern" "$file" 2>/dev/null | grep -v REDACTED || true)
    if [[ -n "$matches" ]]; then
      while IFS= read -r line; do
        case "$pattern" in
          sk-*) r=$(echo "$line" | sed -E 's/sk-[A-Za-z0-9_-]{20,}/sk-REDACTED/g') ;;
          ghp-*) r=$(echo "$line" | sed -E 's/ghp_[A-Za-z0-9]{36}/ghp-REDACTED/g') ;;
          github_pat-*) r=$(echo "$line" | sed -E 's/github_pat_[A-Za-z0-9_-]{220}/github_pat-REDACTED/g') ;;
          AIza*) r=$(echo "$line" | sed -E 's/AIza[A-Za-z0-9_-]+/AIza-REDACTED/g') ;;
          xai-*) r=$(echo "$line" | sed -E 's/xai-[A-Za-z0-9_-]+/xai-REDACTED/g') ;;
          gsk-*) r=$(echo "$line" | sed -E 's/gsk_[A-Za-z0-9_-]+/gsk-REDACTED/g') ;;
          Bearer*) r=$(echo "$line" | sed -E 's/Bearer [A-Za-z0-9_.-]+/Bearer REDACTED/g') ;;
        esac
        echo "REDACTED match in $file:"
        echo "$r" | grep -E "$pattern" || true
      done <<< "$matches"
      FOUND=1
    fi
  done
done < <(git ls-files 2>/dev/null)

if [[ $FOUND -eq 1 ]]; then
  echo "Secrets detected"
  exit 1
else
  echo "No secrets detected"
  exit 0
fi
