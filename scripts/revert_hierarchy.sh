#!/usr/bin/env bash
# revert_hierarchy.sh — remove the agent-kit hierarchy setup and restore a plain
# plan/build opencode config.
#
# Run on the machine that installed the hierarchy plugin:
#   bash revert_hierarchy.sh            # cleans the global config
#   bash revert_hierarchy.sh [projdir]  # also cleans <projdir>/opencode.json
#
# What it removes:
#   - hierarchy agents (monarch, bishop, knight, mercenary, p8-worker, witch,
#     mage, grand-bishop, knight-commander) from the global config
#   - the hierarchy-guard plugin link and the other installer symlinks
#   - the p8-agent / p8-agent-fast providers, when the private p8 key file is
#     absent (that provider block is what usually leaves a fresh install stuck)
#   - hierarchy agents from a project-local opencode.json, when given
#
# What it restores:
#   - build and plan back to plain opencode agents (mode primary, no pinned
#     system prompt, no permission restrictions, model inherited)
#
# Every file it touches is backed up first: <file>.bak-<timestamp>-pre-revert
set -uo pipefail

CFG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.json}"
PROJ="${1:-}"
TS="$(date +%Y%m%d-%H%M%S)"

say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- config rewrite
CLEAN_JS='
const fs = require("fs");
const p = process.argv[1];
let cfg;
try { cfg = JSON.parse(fs.readFileSync(p, "utf8")); }
catch (e) { console.error("not valid JSON, left untouched"); process.exit(1); }

const HIERARCHY = ["monarch","bishop","knight","mercenary","p8-worker","witch","mage","grand-bishop","knight-commander"];
for (const key of ["agents","agent"]) {
  const bag = cfg[key];
  if (!bag || typeof bag !== "object") continue;
  for (const name of HIERARCHY) delete bag[name];
  // Drop the build/plan overrides outright so opencode falls back to its own
  // built-in plan and build agents (primary, unrestricted, inherited model).
  for (const name of ["build","plan"]) delete bag[name];
}
if (cfg.agents && Object.keys(cfg.agents).length === 0) delete cfg.agents;
if (cfg.agent && Object.keys(cfg.agent).length === 0) delete cfg.agent;

// The p8 providers need a private key file. Without it opencode cannot start,
// which is the usual reason a fresh public-repo install feels "stuck".
const keyFile = process.env.HOME + "/.config/opencode/secrets/p8-api-key";
let haveKey = false;
try { haveKey = fs.existsSync(keyFile); } catch {}
if (!haveKey && cfg.providers) {
  delete cfg.providers["p8-agent"];
  delete cfg.providers["p8-agent-fast"];
  if (typeof cfg.model === "string" && cfg.model.startsWith("p8-agent")) delete cfg.model;
  console.log("removed p8-agent providers + pinned model (no p8 key at " + keyFile + ")");
}

fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
console.log("hierarchy agents removed, build/plan overrides dropped (opencode built-ins apply again)");
'

clean_config() {
  local file="$1"
  [ -f "$file" ] || return 0
  local bak="${file}.bak-${TS}-pre-revert"
  cp -p "$file" "$bak" || return 0
  say "backup: $bak"
  if node -e "$CLEAN_JS" "$file"; then
    say "rewrote: $file"
  else
    say "rewrite failed, left as-is: $file"
  fi
}

# ---------------------------------------------------------------- 1. global config
if [ -f "$CFG" ]; then
  clean_config "$CFG"
else
  say "no global config at $CFG (nothing to clean)"
fi

# ---------------------------------------------------------------- 2. plugin + symlinks
PLUGIN_DIR="$HOME/.config/opencode/plugins"
if [ -e "${PLUGIN_DIR:?}/hierarchy-guard" ] || [ -L "${PLUGIN_DIR:?}/hierarchy-guard" ]; then
  rm -rf "${PLUGIN_DIR:?}/hierarchy-guard"
  say "removed plugin link: $PLUGIN_DIR/hierarchy-guard"
fi

for link in \
  "$HOME/.config/opencode/AGENTS.md" \
  "$HOME/.config/opencode/cli.json" \
  "$HOME/.local/bin/beeper-mcp-shim" \
  "$HOME/.local/bin/tailshare" \
  "$PLUGIN_DIR/tailshare" \
  "$PLUGIN_DIR/budget-guard"
do
  if [ -L "$link" ]; then
    rm -f "$link" && say "removed link: $link"
  fi
done

for dir in "$HOME/.config/opencode/skills" "$HOME/.agents/skills"; do
  [ -d "$dir" ] || continue
  for skill in "$dir"/*; do
    [ -L "$skill" ] || continue
    target="$(readlink "$skill")"
    case "$target" in
      *agent-kit*) rm -f "$skill" && say "removed skill link: $skill" ;;
    esac
  done
done

# ---------------------------------------------------------------- 3. project config
if [ -n "$PROJ" ]; then
  for file in "$PROJ/opencode.json" "$PROJ/opencode.base.json"; do
    [ -f "$file" ] || continue
    clean_config "$file"
  done
fi

# ---------------------------------------------------------------- 4. summary
say ""
say "done. Restart opencode."
say "  - build and plan are plain opencode agents again (no hierarchy routing)"
say "  - hierarchy-guard plugin and installer symlinks are gone"
say "  - your own files were never touched; backups are *.bak-${TS}-pre-revert"
say "  - to remove the clone too: rm -rf <path-to-agent-kit>"
