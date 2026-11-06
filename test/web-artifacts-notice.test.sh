#!/usr/bin/env bash
# Offline checks for the bundled shadcn/ui components: the license and notice
# must be present, pinned by hash, and copied into generated projects; the
# installer must refuse before creating anything when they are missing.
# No network, no real pnpm: the script is driven with a stub on PATH.
# Run: bash test/web-artifacts-notice.test.sh (or npm run test:notice)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPTS="$ROOT/skills/web-artifacts-builder/scripts"
TARBALL="$SCRIPTS/shadcn-components.tar.gz"
LICENSE="$SCRIPTS/SHADCN-UI-LICENSE.txt"
NOTICE="$SCRIPTS/SHADCN-UI-NOTICE.md"
INIT="$SCRIPTS/init-artifact.sh"
fail=0

check() {
  if [ "$2" = "$3" ]; then
    printf 'ok   - %s\n' "$1"
  else
    printf 'FAIL - %s (expected %s, got %s)\n' "$1" "$3" "$2"
    fail=1
  fi
}

contains() {
  if grep -qF -- "$2" "$3" 2>/dev/null; then
    printf 'ok   - %s\n' "$1"
  else
    printf 'FAIL - %s (missing %s in %s)\n' "$1" "$2" "$3"
    fail=1
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- the files themselves -------------------------------------------------
check "license file present" "yes" "$([ -s "$LICENSE" ] && echo yes || echo no)"
check "notice file present" "yes" "$([ -s "$NOTICE" ] && echo yes || echo no)"
check "license is the shadcn/ui MIT text" \
  "1564074e13439397221ffd522e2e504d56561994a23d371aa5e3ad43e4f5423f" \
  "$(sha256sum "$LICENSE" | cut -d' ' -f1)"
check "archive hash unchanged" \
  "1296f069e825de7933de7e8ac63672495cacce3f88f2dc45554dbcddbcbca60f" \
  "$(sha256sum "$TARBALL" | cut -d' ' -f1)"
contains "license carries the copyright line" "Copyright (c) 2023 shadcn" "$LICENSE"
contains "license grants permission" "Permission is hereby granted" "$LICENSE"
contains "notice names the archive hash" \
  "1296f069e825de7933de7e8ac63672495cacce3f88f2dc45554dbcddbcbca60f" "$NOTICE"

# The tarball must stay byte-identical to what upstream publishes, so the
# notices cannot live inside it. If this ever reports 1, the archive was
# repacked and the provenance claim in docs/provenance.md is wrong.
check "no license/notice inside the archive" "0" \
  "$(tar -tzf "$TARBALL" | grep -ci -e 'license' -e 'notice' || true)"

# --- preflight precedes side effects --------------------------------------
preflight_line=$(grep -n 'SHADCN-UI-LICENSE.txt SHADCN-UI-NOTICE.md' "$INIT" | head -1 | cut -d: -f1)
first_pnpm_line=$(grep -n '^pnpm ' "$INIT" | head -1 | cut -d: -f1)
check "notice preflight is before the first pnpm call" "yes" \
  "$([ -n "$preflight_line" ] && [ -n "$first_pnpm_line" ] && [ "$preflight_line" -lt "$first_pnpm_line" ] && echo yes || echo no)"

# --- stub pnpm, in a sandbox copy of scripts/ -----------------------------
STUB="$TMP/bin"
mkdir -p "$STUB"
cat > "$STUB/pnpm" <<'STUB_PNPM'
#!/bin/bash
printf '%s\n' "$*" >> "$PNPM_LOG"
# Emulate `pnpm create vite NAME --template react-ts`: lay down the few
# template files init-artifact.sh edits.
if [ "$1" = "create" ] && [ "$2" = "vite" ]; then
  name="$3"
  mkdir -p "$name/src"
  printf '<!doctype html>\n<link rel="icon" href="/vite.svg">\n<title>vite app</title>\n' > "$name/index.html"
  printf '{"compilerOptions":{}}\n' > "$name/tsconfig.json"
  printf '{"compilerOptions":{}}\n' > "$name/tsconfig.app.json"
  printf 'export default function App() { return null }\n' > "$name/src/App.tsx"
fi
exit 0
STUB_PNPM
chmod +x "$STUB/pnpm"

run_init() {
  # $1 = scripts dir to run, $2 = work dir
  ( cd "$2" && PATH="$STUB:$PATH" PNPM_LOG="$2/pnpm.log" bash "$1/init-artifact.sh" demo >"$2/out.txt" 2>&1 )
}

# Success path: components land with the notice beside them.
good_scripts="$TMP/good-scripts"
mkdir -p "$good_scripts"
cp "$INIT" "$TARBALL" "$LICENSE" "$NOTICE" "$good_scripts/"
mkdir -p "$TMP/good"
run_init "$good_scripts" "$TMP/good"
rc=$?
check "install with notices exits 0" "0" "$rc"
check "extracts the 43 components" "43" "$(ls "$TMP/good/demo/src/components/ui"/*.tsx 2>/dev/null | wc -l | tr -d ' ')"
check "copies the license into the project" "yes" \
  "$([ -f "$TMP/good/demo/src/components/ui/SHADCN-UI-LICENSE.txt" ] && echo yes || echo no)"
check "copies the notice into the project" "yes" \
  "$([ -f "$TMP/good/demo/src/components/ui/SHADCN-UI-NOTICE.md" ] && echo yes || echo no)"
check "copied license is byte-identical" "yes" \
  "$(cmp -s "$LICENSE" "$TMP/good/demo/src/components/ui/SHADCN-UI-LICENSE.txt" && echo yes || echo no)"

# Missing license: fail before any project or pnpm work happens.
bad_scripts="$TMP/bad-scripts"
mkdir -p "$bad_scripts"
cp "$INIT" "$TARBALL" "$NOTICE" "$bad_scripts/"
mkdir -p "$TMP/bad"
run_init "$bad_scripts" "$TMP/bad"
rc=$?
check "missing license exits non-zero" "yes" "$([ "$rc" -ne 0 ] && echo yes || echo no)"
contains "missing license names the file" "SHADCN-UI-LICENSE.txt" "$TMP/bad/out.txt"
check "missing license creates no project" "no" \
  "$([ -e "$TMP/bad/demo" ] && echo yes || echo no)"
check "missing license invokes no pnpm" "no" \
  "$([ -e "$TMP/bad/pnpm.log" ] && echo yes || echo no)"

# Missing notice: same, and the failure names the notice.
bad2_scripts="$TMP/bad2-scripts"
mkdir -p "$bad2_scripts"
cp "$INIT" "$TARBALL" "$LICENSE" "$bad2_scripts/"
mkdir -p "$TMP/bad2"
run_init "$bad2_scripts" "$TMP/bad2"
rc=$?
check "missing notice exits non-zero" "yes" "$([ "$rc" -ne 0 ] && echo yes || echo no)"
contains "missing notice names the file" "SHADCN-UI-NOTICE.md" "$TMP/bad2/out.txt"
check "missing notice invokes no pnpm" "no" \
  "$([ -e "$TMP/bad2/pnpm.log" ] && echo yes || echo no)"

if [ "$fail" -eq 0 ]; then
  echo "all checks passed"
else
  echo "checks failed"
fi
exit "$fail"
