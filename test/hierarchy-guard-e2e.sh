#!/usr/bin/env bash
# hierarchy-guard end-to-end harness.
#
# Proves, against the real opencode binary, that:
#   1. an agent whose configured policy denies `shell` cannot run the command
#      (runtime removes/denies the tool);
#   2. a control agent with no policy runs it;
#   3. THE HARD CASE: when a client appends a SESSION-level allow that the
#      runtime's own last-match-wins resolution accepts (the T3 Code failure
#      mode), the plugin's permission.evaluate hook still forces a deny.
#
# Everything runs in a throwaway HOME under /tmp/opencode/hg-e2e with a stripped
# environment (`env -i`), so the live background service and live config are
# never touched.
#
# Usage:
#   test/hierarchy-guard-e2e.sh                 # scenarios 1-3
#   test/hierarchy-guard-e2e.sh --hard          # also the session-override case
#   KEEP=1 test/hierarchy-guard-e2e.sh --hard   # keep the scratch tree
#
# Not wired into `npm test`: it needs network access to a free model and the
# opencode binary.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="${HG_ROOT:-/tmp/opencode/hg-e2e}"
OPENCODE_BIN="${OPENCODE_BIN:-/usr/bin/opencode}"
PLUGIN_SRC="$REPO_ROOT/plugins/hierarchy-guard"
MODEL="${HG_MODEL:-opencode/nemotron-3.5-super-vl-free}"
SYSTEM="Use the bash tool to run the command you are given. Report output verbatim."
TIMEOUT="${HG_TIMEOUT:-240}"
KEEP="${KEEP:-0}"
RUN_HARD=0
[[ "${1:-}" == "--hard" ]] && RUN_HARD=1

HOME_DIR="$ROOT/home"
CFG_DIR="$HOME_DIR/.config/opencode"
CFG="$CFG_DIR/opencode.json"
WS="$ROOT/ws"
LOGS="$ROOT/logs"

log() { printf '\n=== %s ===\n' "$*"; }

# oc <args...> — run opencode in the stripped throwaway environment.
oc() {
  ( cd "$WS" || exit 1
    timeout "$TIMEOUT" env -i \
      PATH=/usr/local/bin:/usr/bin:/bin \
      HOME="$HOME_DIR" \
      TERM=xterm-256color \
      "$OPENCODE_BIN" "$@" )
}

# write_config <shell-deny> <edit-deny>
write_config() {
  local shell_deny="$1" edit_deny="$2"
  local edit_rule=""
  [[ "$edit_deny" == "1" ]] && edit_rule=',
        { "action": "edit", "resource": "*", "effect": "deny" }'
  mkdir -p "$CFG_DIR/plugins" "$WS" "$LOGS"
  cat >"$CFG" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "agents": {
    "guardedsubj": {
      "mode": "primary",
      "model": "$MODEL",
      "system": "$SYSTEM",
      "permissions": [
        { "action": "shell", "resource": "*", "effect": "deny" }$edit_rule
      ]
    },
    "control": {
      "mode": "primary",
      "model": "$MODEL",
      "system": "$SYSTEM"
    }
  }
}
JSON
  ln -sfn "$PLUGIN_SRC" "$CFG_DIR/plugins/hierarchy-guard"
}

run_agent() {
  # $1 = label, $2 = marker filename; rest = opencode run args
  local label="$1" marker="$2"; shift 2
  local out="$LOGS/$label.out"
  rm -f "$WS/$marker"
  oc run --standalone "$@" >"$out" 2>&1
  local rc=$?
  local exists=no
  [[ -f "$WS/$marker" ]] && exists=yes
  log "run: $label (exit=$rc, $marker=$exists)"
  cat "$out"
}

log "setup"
rm -rf "$ROOT"

# --- scenario 1: exactly the spec's policy (shell denied, write allowed) ----
write_config 1 0
echo "root=$ROOT  plugin -> $(readlink "$CFG_DIR/plugins/hierarchy-guard")"
python3 -m json.tool "$CFG" >/dev/null && echo "config JSON: valid" || echo "config JSON: INVALID"
run_agent 1-spec-guardedsubj subj-spec.txt --agent guardedsubj \
  "Run this exact bash command and report its output verbatim: echo HG_TEST > $WS/subj-spec.txt"

# --- scenario 2: strict policy (shell + edit denied) so only a real shell ---
#     call could create the marker
write_config 1 1
run_agent 2-strict-guardedsubj subj-strict.txt --agent guardedsubj \
  "Run this exact bash command and report its output verbatim: echo HG_TEST > $WS/subj-strict.txt"
run_agent 3-control control.txt --agent control \
  "Run this exact bash command and report its output verbatim: echo HG_TEST > $WS/control.txt"

if [[ "$RUN_HARD" == "1" ]]; then
  # --- scenario 4: the T3 Code failure mode --------------------------------
  # Agent rules deny shell; a client appends a SESSION-level allow that wins
  # under the runtime's last-match-wins rules, so the shell tool IS present.
  # Only the plugin's evaluate hook can still deny it.
  log "hard case: session-level allow overrides the agent's shell deny"
  sid="$(oc api --standalone session.create \
          -d "{\"agent\":\"guardedsubj\",\"title\":\"hardcase\"}" \
        | node -e 'let s="";process.stdin.on("data",(d)=>s+=d).on("end",()=>{try{process.stdout.write(JSON.parse(s).data.id)}catch{process.stdout.write("")}})')"
  echo "session: $sid"
  oc api --standalone session.update --param "sessionID=$sid" \
    -d '{"permissions":[{"action":"shell","resource":"*","effect":"allow"}]}' >/dev/null 2>&1
  oc api --standalone session.get --param "sessionID=$sid" | head -c 400; echo
  run_agent 4-hard-session-allow subj-hard.txt --session "$sid" \
    "Run this exact bash command and report its output verbatim: echo HG_TEST > $WS/subj-hard.txt"
fi

log "summary"
echo "1 spec guardedsubj  marker subj-spec.txt:   $([[ -f "$WS/subj-spec.txt" ]] && echo PRESENT || echo absent)"
echo "2 strict guardedsubj marker subj-strict.txt: $([[ -f "$WS/subj-strict.txt" ]] && echo PRESENT || echo absent)"
echo "3 control            marker control.txt:     $([[ -f "$WS/control.txt" ]] && echo PRESENT || echo absent)"
[[ "$RUN_HARD" == "1" ]] && echo "4 hard guardedsubj   marker subj-hard.txt:   $([[ -f "$WS/subj-hard.txt" ]] && echo PRESENT || echo absent)"
echo "logs: $LOGS"

if [[ "$KEEP" != "1" ]]; then
  rm -rf "$ROOT"
  echo "cleaned $ROOT"
fi
