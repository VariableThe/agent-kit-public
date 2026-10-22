#!/usr/bin/env bash
# Bootstrap agent-kit on a fresh device. Safe to re-run (idempotent).
#
#   1. ./install.sh --apply           OpenCode config + symlinks (forwards extra
#                                     args, e.g. --role beeper-client)
#   2. key.env bootstrap              create ~/.pi/agent/key.env if missing
#                                     (seed from the OpenCode p8 secret file or
#                                     prompt); mode 600; never printed
#   3. pi/install.sh --make-default   Pi harness config + CLI
#   4. re-pin kimi-k3:cloud           --make-default sets router/agent; restore
#                                     the repo default Pi keeps
#   5. scripts/check-pi.sh            read-only health report
#
# Order note: the Pi installer requires an existing key.env (distribution
# bundles ship no secret), so step 2 must run before step 3.
#
# No secret is ever printed or stored in this repo.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PI_CONFIG_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
KEY_ENV="$PI_CONFIG_DIR/key.env"
SECRET_FILE="$HOME/.config/opencode/secrets/p8-api-key"

die() { echo "bootstrap: $*" >&2; exit 1; }

# --- preflight: everything needed is present before any writes --------------
command -v node >/dev/null 2>&1 || die "node is required (OpenCode already needs it)"
command -v uv >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 \
  || die "uv or python3 (3.10+) is required for the Pi installer"
command -v bun >/dev/null 2>&1 || [[ -x "$HOME/.bun/bin/bun" ]] \
  || die "bun is required for the Pi CLI install (https://bun.sh), then re-run bootstrap"

# --- 1. OpenCode config ------------------------------------------------------
echo "==> 1/5 apply OpenCode config"
"$REPO_ROOT/install.sh" --apply "$@"

# --- 2. key.env bootstrap ----------------------------------------------------
echo "==> 2/5 ensure $KEY_ENV"
if [[ -f "$KEY_ENV" ]]; then
  echo "    key.env exists; leaving it untouched"
else
  mkdir -p "$PI_CONFIG_DIR"
  chmod 700 "$PI_CONFIG_DIR"
  key=""
  if [[ -f "$SECRET_FILE" ]]; then
    # Command substitution strips trailing newlines; value is never echoed.
    key="$(cat "$SECRET_FILE")"
    echo "    seeded from $SECRET_FILE"
  elif [[ -t 0 ]]; then
    read -r -s -p "    Paste P8 router key (input hidden): " key
    echo
  else
    die "no key source: create $KEY_ENV from $REPO_ROOT/pi/key.env.example (mode 600) and re-run bootstrap"
  fi
  if ! [[ "$key" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    unset key
    die "invalid key format (expected [A-Za-z0-9_.-]+); not written"
  fi
  tmp="$(mktemp "$PI_CONFIG_DIR/.key.env.XXXXXX")"
  printf 'P8_ROUTER_API_KEY=%s\n' "$key" > "$tmp"
  unset key
  chmod 600 "$tmp"
  mv -f "$tmp" "$KEY_ENV"
  chmod 600 "$KEY_ENV"
  echo "    wrote $KEY_ENV (mode 600)"
fi
chmod 600 "$KEY_ENV"   # step (d): enforce even on pre-existing files

# --- 3. Pi install -----------------------------------------------------------
echo "==> 3/5 install Pi config + CLI"
if ! sh "$REPO_ROOT/pi/install.sh" --make-default; then
  echo "    live discovery failed; retrying with the bundled snapshot (--offline)" >&2
  sh "$REPO_ROOT/pi/install.sh" --make-default --offline
fi

# --- 4. restore the kimi-k3:cloud default ------------------------------------
# --make-default deliberately selects router/agent; this repo's Pi default is
# kimi-k3:cloud (docs/PI_CONFIG.md). Re-apply so check-pi.sh stays healthy.
echo "==> 4/5 set Pi default to p8-agent/kimi-k3:cloud"
node -e '
const fs = require("fs");
const p = process.argv[1];
const s = JSON.parse(fs.readFileSync(p, "utf8"));
s.defaultProvider = "p8-agent";
s.defaultModel = "kimi-k3:cloud";
const want = ["p8-agent/kimi-k3:cloud", "p8-agent/router/agent"];
const have = Array.isArray(s.enabledModels) ? s.enabledModels : [];
s.enabledModels = [...new Set([...want, ...have])];
fs.writeFileSync(p, JSON.stringify(s, null, 2) + "\n", { mode: 0o600 });
' "$PI_CONFIG_DIR/settings.json"
chmod 600 "$PI_CONFIG_DIR/settings.json"

# --- 5. health + PATH note ---------------------------------------------------
echo "==> 5/5 health check"
status=0
bash "$REPO_ROOT/scripts/check-pi.sh" || status=$?
launcher="$HOME/.local/bin/pi"
resolved="$(command -v pi 2>/dev/null || true)"
if [[ -n "$resolved" && "$resolved" != "$launcher" ]]; then
  echo "warning: 'pi' resolves to $resolved, not $launcher" >&2
  echo "         put $HOME/.local/bin earlier on PATH or invoke $launcher explicitly" >&2
elif [[ -z "$resolved" ]]; then
  echo "note: add $HOME/.local/bin to PATH to run pi" >&2
fi
exit "$status"
