#!/usr/bin/env bash
# Exercises scripts/revert_hierarchy.sh against a fake HOME.
# Run: bash test/revert-hierarchy.test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$ROOT/scripts/revert_hierarchy.sh"
fail=0

check() {
  if [ "$2" = "$3" ]; then
    printf 'ok   - %s\n' "$1"
  else
    printf 'FAIL - %s (expected %s, got %s)\n' "$1" "$3" "$2"
    fail=1
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME_DIR="$TMP/home"
CLONE="$TMP/agent-kit"
mkdir -p "$HOME_DIR/.config/opencode/plugins" "$HOME_DIR/.config/opencode/skills" \
  "$HOME_DIR/.agents/skills" "$CLONE/plugins/hierarchy-guard" "$CLONE/skills/beeper" \
  "$TMP/project"
echo agents > "$CLONE/AGENTS.md"

cat > "$HOME_DIR/.config/opencode/opencode.json" <<'JSON'
{"agents":{"build":{"system":"Monarch"},"plan":{"system":"Bishop"},"bishop":{},
"knight":{},"monarch":{},"worker":{}},"model":"p8-agent/router/agent",
"providers":{"p8-agent":{},"p8-agent-fast":{},"openrouter":{}}}
JSON
cp "$HOME_DIR/.config/opencode/opencode.json" "$TMP/project/opencode.json"
ln -s "$CLONE/plugins/hierarchy-guard" "$HOME_DIR/.config/opencode/plugins/hierarchy-guard"
ln -s "$CLONE/skills/beeper" "$HOME_DIR/.config/opencode/skills/beeper"

HOME="$HOME_DIR" bash "$SCRIPT" "$TMP/project" > "$TMP/out.log" 2>&1
status=$?

GLOBAL="$HOME_DIR/.config/opencode/opencode.json"
PROJECT="$TMP/project/opencode.json"
json_get() { node -e "const c=JSON.parse(require('fs').readFileSync('$1','utf8'));console.log($2)"; }

check "exit status" "$status" "0"
check "global config is valid JSON" "$(node -e "JSON.parse(require('fs').readFileSync('$GLOBAL','utf8'));console.log('yes')" 2>/dev/null)" "yes"
check "hierarchy agents removed" "$(json_get "$GLOBAL" "Object.keys(c.agents||{}).join(',')")" "worker"
check "build/plan overrides removed" "$(json_get "$GLOBAL" "['build','plan'].filter(k=>k in (c.agents||{})).length")" "0"
check "p8 providers removed without a key" "$(json_get "$GLOBAL" "Object.keys(c.providers||{}).join(',')")" "openrouter"
check "pinned p8 model removed" "$(json_get "$GLOBAL" "String(c.model)")" "undefined"
check "backup written" "$(ls "$GLOBAL".bak-*-pre-revert 2>/dev/null | wc -l)" "1"
check "project config cleaned" "$(json_get "$PROJECT" "Object.keys(c.agents||{}).join(',')")" "worker"
check "plugin link removed" "$([ -e "$HOME_DIR/.config/opencode/plugins/hierarchy-guard" ] && echo present || echo gone)" "gone"
check "skill link removed" "$([ -e "$HOME_DIR/.config/opencode/skills/beeper" ] && echo present || echo gone)" "gone"
check "cloned repo untouched" "$([ -f "$CLONE/AGENTS.md" ] && echo intact || echo lost)" "intact"
check "cloned skill untouched" "$([ -d "$CLONE/skills/beeper" ] && echo intact || echo lost)" "intact"

if [ "$fail" -eq 0 ]; then
  echo "all checks passed"
else
  echo "checks failed" >&2
  sed 's/^/  | /' "$TMP/out.log" >&2
fi
exit "$fail"
