#!/usr/bin/env bash
# revert_hierarchy.sh — remove the agent-kit hierarchy setup and restore a plain
# plan/build opencode config.
#
# Run on the machine that installed the hierarchy plugin:
#   bash revert_hierarchy.sh            # cleans the global config
#   bash revert_hierarchy.sh [projdir]  # also cleans <projdir>/opencode.json
#
# What it removes:
#   - hierarchy agents (monarch, bishop, knight, mercenary, witch, mage,
#     grand-bishop, knight-commander) from the global config
#   - the hierarchy-guard plugin link and the installer symlinks
#   - hierarchy agents from a project-local opencode.json, when given
#
# It does not touch provider blocks, models or credentials: only agent entries
# and symlinks this repo's installer creates.
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

const HIERARCHY = ["monarch","bishop","knight","mercenary","witch","mage","grand-bishop","knight-commander"];
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
  "$PLUGIN_DIR/tailshare"
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
    # Only symlinks pointing into an agent-kit checkout, identified by its own
    # installer files. A lookalike path (my-agent-kit-notes/skills/...) is left
    # alone rather than matched by name.
    case "$target" in
      */skills/*) repo_root="${target%/skills/*}" ;;
      *) continue ;;
    esac
    if [ -f "$repo_root/install.sh" ] && [ -f "$repo_root/AGENTS.md" ]; then
      rm -f "$skill" && say "removed skill link: $skill"
    fi
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
