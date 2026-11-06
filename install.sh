#!/usr/bin/env bash
# agent-kit installer (bash + node only)
#
# Symlinks the parts of this repo an OpenCode install can use: the global
# AGENTS.md, every skill under ./skills, and the two OpenCode plugins under
# ./plugins. If this repo also carries opencode.base.json and overlays/,
# the installer merges them into the live opencode.json as well.
#
# Everything is symlinked back into place, so this repo stays the single
# source of truth; re-run the installer after an edit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BASE_FILE="$REPO_ROOT/opencode.base.json"
OVERLAYS_DIR="$REPO_ROOT/overlays"
SKILLS_DIR="$REPO_ROOT/skills"
LIVE_FILE="$HOME/.config/opencode/opencode.json"
HOST_FILE="$HOME/.config/agent-kit/host.json"
TAILSHARE_PLUGIN_SRC="$REPO_ROOT/plugins/tailshare"
TAILSHARE_PLUGIN_LINK="$HOME/.config/opencode/plugins/tailshare"
HIERARCHY_GUARD_PLUGIN_SRC="$REPO_ROOT/plugins/hierarchy-guard"
HIERARCHY_GUARD_PLUGIN_LINK="$HOME/.config/opencode/plugins/hierarchy-guard"
SKILL_LINK_DIRS=("$HOME/.config/opencode/skills" "$HOME/.agents/skills")

# Config management is optional: only a repo that ships a base config and an
# overlays/ directory manages opencode.json. Everything else is still linkable.
CONFIG_MANAGED=0
if [[ -f "$BASE_FILE" && -d "$OVERLAYS_DIR" ]]; then
  CONFIG_MANAGED=1
fi

usage() {
  cat <<EOF
agent-kit installer (bash + node only)

Usage:
  ./install.sh [--check] [--apply] [--role <name>] [--force] [--uninstall] [--help] [--dry-run]

Modes (default is read-only):
  --check        Report drift between this repo and the machine; exit 1 on drift, 0 when clean.
                 This is the default when no flags are given. Creates no backups and writes nothing.
  --dry-run      Alias for --check.
  --apply        Write changes: link AGENTS.md, the plugins, and project the ./skills symlinks.
                 When this repo ships opencode.base.json + overlays/, also merge them into
                 $HOME/.config/opencode/opencode.json (the only mode that writes it).
  --role <name>  Use overlay overlays/<name>.json for this run. Only available when this repo
                 ships overlays/. When combined with --apply and the apply succeeds, the role is
                 persisted to $HOME/.config/agent-kit/host.json as {"role": "<name>"}. Without
                 --apply it only affects that run's check.
                 Role resolution: --role flag > host.json > "plain".
  --force        Bypass the refusal guards (temporary-checkout guard, drop-live-config guard).
  --uninstall    Remove only installer-created symlinks (AGENTS.md, plugin links, skill links).
                 Never deletes opencode.json itself. Refuses from a temporary checkout unless --force.
  --help         Show this help and exit 0.

Config management in this repo:
$(if [[ "$CONFIG_MANAGED" -eq 1 ]]; then
    printf '  enabled: opencode.base.json + overlays/ found; --apply merges them into opencode.json.\n'
  else
    printf '  disabled: no opencode.base.json / overlays/ here, so opencode.json is never touched.\n'
    printf '  The installer only links AGENTS.md, skills/ and plugins/.\n'
  fi)

What gets linked:
  AGENTS.md                         -> ~/.config/opencode/AGENTS.md   (global agent rules)
  skills/<name>                     -> ~/.config/opencode/skills/<name> and ~/.agents/skills/<name>
  plugins/tailshare                 -> ~/.config/opencode/plugins/tailshare (auto-discovered, no config change)
  plugins/hierarchy-guard           -> ~/.config/opencode/plugins/hierarchy-guard (auto-discovered, no config change)

Refusal guards (read-only by default):
  1. Writing is opt-in: only --apply writes anything. --check / default writes nothing.
  2. Refuse to drop live config (only when config management is enabled): before writing, the
     merged result is compared against the live file. If the merge would remove any mcp.servers
     entry (or top-level key) present live, the installer refuses, exits non-zero, writes nothing,
     and names the entry plus the fix (--role <name> or --force).
  3. Refuse from a temporary checkout: if the repo root is a linked git worktree
     (git rev-parse --git-dir differs from --git-common-dir) or lives under a worktrees
     directory, the installer refuses unless --force, because symlinks into a worktree
     dangle once the worktree is removed.

Examples:
  ./install.sh --check
  ./install.sh --apply
  ./install.sh --uninstall
EOF
}

MODE="check"
ROLE_ARG=""
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --dry-run) MODE="check"; shift ;;
    --apply) MODE="apply"; shift ;;
    --role)
      if [[ $# -lt 2 ]]; then echo "error: --role requires a name" >&2; exit 2; fi
      ROLE_ARG="$2"; shift 2 ;;
    --role=*) ROLE_ARG="${1#--role=}"; shift ;;
    --force) FORCE=1; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "error: unknown flag: $1 (see --help)" >&2; exit 2 ;;
  esac
done

if ! command -v node >/dev/null 2>&1; then
  echo "error: node is required (opencode already requires it)" >&2
  exit 1
fi

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "error: missing $SKILLS_DIR/" >&2
  exit 1
fi

if [[ "$CONFIG_MANAGED" -eq 0 && -n "$ROLE_ARG" ]]; then
  echo "error: --role needs overlays/, which this repo does not ship" >&2
  echo "error: this repo links rules, skills and plugins only; opencode.json is not managed here" >&2
  exit 2
fi

resolve_role() {
  if [[ -n "$ROLE_ARG" ]]; then
    printf '%s' "$ROLE_ARG"
    return
  fi
  if [[ -f "$HOST_FILE" ]]; then
    local r
    r="$(node -e 'const fs=require("fs"); try { const raw=fs.readFileSync(process.argv[1],"utf8"); try { const j=JSON.parse(raw); if (j && typeof j.role==="string" && j.role.length>0) { console.log(j.role); } else { console.log(raw.trim()); } } catch { console.log(raw.trim()); } } catch { console.log("plain"); }' "$HOST_FILE" 2>/dev/null || printf 'plain')"
    if [[ -n "$r" ]]; then
      printf '%s' "$r"
      return
    fi
  fi
  printf 'plain'
}

ROLE="$(resolve_role)"

if [[ "$CONFIG_MANAGED" -eq 1 && "$MODE" != "uninstall" ]]; then
  if [[ ! -f "$OVERLAYS_DIR/$ROLE.json" ]]; then
    echo "error: unknown role '$ROLE' (no overlays/$ROLE.json)" >&2
    echo "available roles:" >&2
    ls "$OVERLAYS_DIR"/*.json 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.json$//' >&2 || true
    exit 1
  fi
fi

is_temporary_checkout() {
  case "$REPO_ROOT" in
    */worktrees/*) return 0 ;;
  esac
  local gd gcd gd_abs gcd_abs
  gd="$(git -C "$REPO_ROOT" rev-parse --git-dir 2>/dev/null || true)"
  gcd="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$gd" && -n "$gcd" ]]; then
    case "$gd" in
      /*) gd_abs="$gd" ;;
      *) gd_abs="$REPO_ROOT/$gd" ;;
    esac
    case "$gcd" in
      /*) gcd_abs="$gcd" ;;
      *) gcd_abs="$REPO_ROOT/$gcd" ;;
    esac
    if [[ "$gd_abs" != "$gcd_abs" ]]; then
      return 0
    fi
  fi
  return 1
}

guard_temporary_checkout() {
  if is_temporary_checkout; then
    if [[ "$FORCE" -eq 1 ]]; then
      echo "warning: running from temporary checkout $REPO_ROOT with --force" >&2
      return 0
    fi
    echo "refusing to install from temporary checkout: $REPO_ROOT" >&2
    echo "linked worktrees and paths under a worktrees/ directory dangle once removed." >&2
    echo "Re-run from the canonical clone or pass --force." >&2
    exit 1
  fi
}

merged_to_stdout() {
  node -e '
const fs = require("fs");
const base = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
let overlay = {};
try { overlay = JSON.parse(fs.readFileSync(process.argv[2], "utf8")); } catch (e) { overlay = {}; }
function deepMerge(b, o) {
  const r = Object.assign({}, b);
  for (const k of Object.keys(o)) {
    if (k in r && r[k] !== null && typeof r[k] === "object" && !Array.isArray(r[k]) && o[k] !== null && typeof o[k] === "object" && !Array.isArray(o[k])) {
      r[k] = deepMerge(r[k], o[k]);
    } else {
      r[k] = o[k];
    }
  }
  return r;
}
process.stdout.write(JSON.stringify(deepMerge(base, overlay), null, 2) + "\n");
' "$BASE_FILE" "$OVERLAYS_DIR/$ROLE.json"
}

list_skills() {
  local d
  for d in "$SKILLS_DIR"/*/; do
    [[ -d "$d" ]] || continue
    basename "$d"
  done | sort
}

list_skill_links() {
  local name dir
  while IFS= read -r name; do
    for dir in "${SKILL_LINK_DIRS[@]}"; do
      printf '%s/%s\n' "$dir" "$name"
    done
  done < <(list_skills)
}

# link_file_apply <repo_file> <link_path> <label>
link_file_apply() {
  local repo_file="$1"
  local link_path="$2"
  local label="$3"
  if [[ ! -f "$repo_file" ]]; then
    echo "warning: repo file $repo_file missing, skipping $label" >&2
    return 0
  fi
  mkdir -p "$(dirname "$link_path")"
  if [[ -L "$link_path" ]]; then
    local cur=""
    cur="$(readlink "$link_path" || true)"
    if [[ "$cur" == "$repo_file" ]]; then
      echo "ok $label -> $link_path"
    else
      rm "$link_path"
      ln -s "$repo_file" "$link_path"
      echo "updated $label -> $link_path"
    fi
  elif [[ -e "$link_path" ]]; then
    local bak="$link_path.bak-pre-agent-kit"
    if [[ ! -e "$bak" ]]; then
      cp -p "$link_path" "$bak"
      echo "backed up $link_path to $bak"
    fi
    rm -rf "$link_path"
    ln -s "$repo_file" "$link_path"
    echo "symlinked $label -> $link_path"
  else
    ln -s "$repo_file" "$link_path"
    echo "symlinked $label -> $link_path"
  fi
}

# link_dir_apply <repo_dir> <link_path> <label>
link_dir_apply() {
  local repo_dir="$1"
  local link_path="$2"
  local label="$3"
  if [[ ! -d "$repo_dir" ]]; then
    echo "warning: repo dir $repo_dir missing, skipping $label" >&2
    return 0
  fi
  mkdir -p "$(dirname "$link_path")"
  if [[ -L "$link_path" ]]; then
    local cur=""
    cur="$(readlink "$link_path" || true)"
    if [[ "$cur" == "$repo_dir" ]]; then
      echo "ok $label -> $link_path"
    else
      rm "$link_path"
      ln -s "$repo_dir" "$link_path"
      echo "updated $label -> $link_path"
    fi
  elif [[ -e "$link_path" ]]; then
    echo "warning: skipping $label: real file/dir exists at $link_path, not overwriting" >&2
  else
    ln -s "$repo_dir" "$link_path"
    echo "symlinked $label -> $link_path"
  fi
}

check_link_drift() {
  local drill=0
  for pair in \
    "AGENTS.md:$HOME/.config/opencode/AGENTS.md:$REPO_ROOT/AGENTS.md" \
    "tailshare-plugin:$TAILSHARE_PLUGIN_LINK:$TAILSHARE_PLUGIN_SRC" \
    "hierarchy-guard-plugin:$HIERARCHY_GUARD_PLUGIN_LINK:$HIERARCHY_GUARD_PLUGIN_SRC"; do
    local label="${pair%%:*}"
    local rest="${pair#*:}"
    local link="${rest%%:*}"
    local target="${rest#*:}"
    if [[ -L "$link" ]]; then
      local cur=""
      cur="$(readlink "$link" || true)"
      if [[ "$cur" != "$target" ]]; then
        echo "drift detected: $label symlink points at $cur, expected $target"
        drill=1
      fi
    else
      echo "drift detected: $label is not a symlink to $target"
      drill=1
    fi
  done
  return "$drill"
}

check_skills_drift() {
  local drift=0 name dir dst expected cur
  while IFS= read -r name; do
    expected="$SKILLS_DIR/$name"
    for dir in "${SKILL_LINK_DIRS[@]}"; do
      dst="$dir/$name"
      if [[ -L "$dst" ]]; then
        cur="$(readlink "$dst" || true)"
        if [[ "$cur" != "$expected" ]]; then
          echo "drift detected: skill link $dst points at $cur, expected $expected"
          drift=1
        fi
      elif [[ -e "$dst" ]]; then
        echo "drift detected: $dst exists but is not a symlink (real file/dir, left alone)"
        drift=1
      else
        echo "drift detected: missing skill link $dst -> $expected"
        drift=1
      fi
    done
  done < <(list_skills)
  return "$drift"
}

apply_skills() {
  local name dir dst expected cur
  while IFS= read -r name; do
    expected="$SKILLS_DIR/$name"
    for dir in "${SKILL_LINK_DIRS[@]}"; do
      dst="$dir/$name"
      mkdir -p "$dir"
      if [[ -L "$dst" ]]; then
        cur="$(readlink "$dst" || true)"
        if [[ "$cur" == "$expected" ]]; then
          echo "ok skill $name -> $dst"
        else
          rm "$dst"
          ln -s "$expected" "$dst"
          echo "updated skill $name -> $dst"
        fi
      elif [[ -e "$dst" ]]; then
        echo "warning: skipping $dst: real file/dir exists, not overwriting" >&2
      else
        ln -s "$expected" "$dst"
        echo "symlinked skill $name -> $dst"
      fi
    done
  done < <(list_skills)
}

do_check() {
  guard_temporary_checkout
  local drift=0
  if [[ "$CONFIG_MANAGED" -eq 1 ]]; then
    local tmp
    tmp="$(mktemp)"
    merged_to_stdout > "$tmp"
    if [[ -f "$LIVE_FILE" ]]; then
      if ! cmp -s "$tmp" "$LIVE_FILE"; then
        echo "drift detected: opencode.json differs (base + role '$ROLE' vs live)"
        drift=1
      fi
    else
      echo "drift detected: live $LIVE_FILE is missing"
      drift=1
    fi
    rm -f "$tmp"
  else
    echo "config: not managed by this repo (no opencode.base.json / overlays/); $LIVE_FILE left alone"
  fi

  if ! check_link_drift; then drift=1; fi
  if ! check_skills_drift; then drift=1; fi

  if [[ "$drift" -ne 0 ]]; then
    if [[ "$CONFIG_MANAGED" -eq 1 ]]; then
      echo "check: drift found (role '$ROLE')"
    else
      echo "check: drift found"
    fi
    return 1
  fi
  if [[ "$CONFIG_MANAGED" -eq 1 ]]; then
    echo "check: no drift (role '$ROLE')"
  else
    echo "check: no drift"
  fi
  return 0
}

guard_drop_live_config() {
  local merged_tmp="$1"
  if [[ ! -f "$LIVE_FILE" ]]; then
    return 0
  fi
  local report
  report="$(node -e '
const fs = require("fs");
const merged = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const live = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const missingTop = Object.keys(live).filter((k) => !(k in merged));
const liveServers = (live.mcp && live.mcp.servers) ? Object.keys(live.mcp.servers) : [];
const mergedServers = (merged.mcp && merged.mcp.servers) ? Object.keys(merged.mcp.servers) : [];
const missingServers = liveServers.filter((k) => !mergedServers.includes(k));
if (missingTop.length > 0) console.log("TOP:" + missingTop.join(","));
if (missingServers.length > 0) console.log("SERVERS:" + missingServers.join(","));
' "$merged_tmp" "$LIVE_FILE")"
  if [[ -n "$report" ]]; then
    if [[ "$FORCE" -eq 1 ]]; then
      echo "warning: --force bypasses drop-live-config guard: $report" >&2
      return 0
    fi
    local missing=""
    missing="$(printf '%s\n' "$report" | grep '^SERVERS:' | cut -d: -f2- || true)"
    if [[ -n "$missing" ]]; then
      local first=""
      first="$(printf '%s' "$missing" | cut -d, -f1)"
      echo "refusing to apply role '$ROLE': merged config would remove mcp.servers entry '$first' present in live config." >&2
      echo "Fix with --role <name> or pass --force. Wrote nothing." >&2
      exit 1
    fi
    local missing_top=""
    missing_top="$(printf '%s\n' "$report" | grep '^TOP:' | cut -d: -f2- || true)"
    if [[ -n "$missing_top" ]]; then
      echo "refusing to apply role '$ROLE': merged config would remove top-level key(s) '$missing_top' present in live config." >&2
      echo "Fix with --role <name> or --force. Wrote nothing." >&2
      exit 1
    fi
  fi
}

write_live_config() {
  local tmp
  tmp="$(mktemp)"
  merged_to_stdout > "$tmp"
  guard_drop_live_config "$tmp"

  local live_is_link=0
  if [[ -L "$LIVE_FILE" ]]; then
    live_is_link=1
  fi
  local live_same=0
  if [[ "$live_is_link" -eq 0 && -f "$LIVE_FILE" ]]; then
    if cmp -s "$tmp" "$LIVE_FILE"; then
      live_same=1
    fi
  fi
  if [[ "$live_same" -eq 1 ]]; then
    echo "config unchanged: live opencode.json already matches base + $ROLE overlay"
  else
    if [[ -f "$LIVE_FILE" || -L "$LIVE_FILE" ]]; then
      local ts bak
      ts="$(date +%Y%m%d-%H%M%S)"
      bak="$HOME/.config/opencode/opencode.json.bak-$ts-pre-agent-kit"
      mkdir -p "$(dirname "$LIVE_FILE")"
      cp -p "$LIVE_FILE" "$bak"
      echo "backed up live config to $bak"
    else
      mkdir -p "$(dirname "$LIVE_FILE")"
    fi
    if [[ "$live_is_link" -eq 1 ]]; then
      echo "note: replacing legacy symlink at $LIVE_FILE with a regular file"
      rm -f "$LIVE_FILE"
    fi
    chmod 600 "$tmp"
    mv -f "$tmp" "$LIVE_FILE"
    chmod 600 "$LIVE_FILE"
    echo "wrote $LIVE_FILE (mode 600)"
  fi
  rm -f "$tmp"
}

do_apply() {
  guard_temporary_checkout
  if [[ "$CONFIG_MANAGED" -eq 1 ]]; then
    write_live_config
  else
    echo "config: not managed by this repo (no opencode.base.json / overlays/); $LIVE_FILE left alone"
  fi

  apply_skills
  link_file_apply "$REPO_ROOT/AGENTS.md" "$HOME/.config/opencode/AGENTS.md" "AGENTS.md"
  link_dir_apply "$TAILSHARE_PLUGIN_SRC" "$TAILSHARE_PLUGIN_LINK" "tailshare plugin"
  link_dir_apply "$HIERARCHY_GUARD_PLUGIN_SRC" "$HIERARCHY_GUARD_PLUGIN_LINK" "hierarchy-guard plugin"

  if [[ "$CONFIG_MANAGED" -eq 1 && -n "$ROLE_ARG" ]]; then
    mkdir -p "$(dirname "$HOST_FILE")"
    node -e 'const fs=require("fs"); fs.writeFileSync(process.argv[1], JSON.stringify({role: process.argv[2]}) + "\n");' "$HOST_FILE" "$ROLE"
    echo "persisted role '$ROLE' to $HOST_FILE"
  fi
}

do_uninstall() {
  guard_temporary_checkout
  local removed=0
  local link cur
  for link in "$HOME/.config/opencode/AGENTS.md" "$TAILSHARE_PLUGIN_LINK" "$HIERARCHY_GUARD_PLUGIN_LINK"; do
    if [[ -L "$link" ]]; then
      cur="$(readlink "$link" || true)"
      case "$cur" in
        "$REPO_ROOT"/*)
          rm "$link"
          echo "removed symlink: $link"
          removed=1
          ;;
        *) echo "keeping $link (points outside repo: $cur)" ;;
      esac
    fi
  done
  local link_lines
  link_lines="$(list_skill_links)"
  while IFS= read -r link; do
    if [[ -z "$link" ]]; then continue; fi
    if [[ -L "$link" ]]; then
      cur="$(readlink "$link" || true)"
      case "$cur" in
        "$SKILLS_DIR"/*)
          rm "$link"
          echo "removed skill symlink: $link"
          removed=1
          ;;
        *) echo "keeping $link (points outside repo: $cur)" ;;
      esac
    fi
  done <<EOF
$link_lines
EOF
  if [[ "$removed" -eq 0 ]]; then
    echo "uninstall: no installer-created symlinks found"
  else
    echo "uninstall: removed installer-created symlinks"
  fi
}

case "$MODE" in
  check) do_check ;;
  apply) do_apply ;;
  uninstall) do_uninstall ;;
esac
