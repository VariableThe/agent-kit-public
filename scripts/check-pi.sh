#!/usr/bin/env bash
# Read-only Pi + P8 health check. Exit 0 healthy, 1 defect, 2 not installed.
set -u

PI_BIN="$(command -v pi || true)"
if [[ -z "$PI_BIN" ]]; then
  echo "pi: not installed"
  exit 2
fi

VERSION="$(pi --version 2>/dev/null | head -n1 || true)"
echo "pi: $VERSION"

AGENT_DIR="${HOME}/.pi/agent"
FAIL=0

check_file() {
  local f="$1" policy="$2"
  if [[ ! -f "$AGENT_DIR/$f" ]]; then
    echo "$f: missing"
    FAIL=1
    return
  fi
  local actual
  actual="$(stat -c '%a' "$AGENT_DIR/$f" 2>/dev/null || true)"
  if [[ "$policy" == "strict" ]]; then
    # key.env holds the API key: must be exactly 600.
    if [[ "$actual" != "600" ]]; then
      echo "$f: mode $actual (expected 600)"
      FAIL=1
    else
      echo "$f: ok ($actual)"
    fi
  else
    # Other files: readable and not world-writable suffices on other hosts.
    case "$actual" in
      600|640|644|700)
        echo "$f: ok ($actual)"
        ;;
      *)
        echo "$f: mode $actual (expected one of 600/640/644/700)"
        FAIL=1
        ;;
    esac
  fi
}

for f in models.json settings.json key.env p8-launch.py; do
  if [[ "$f" == "key.env" ]]; then
    check_file "$f" strict
  else
    check_file "$f" relaxed
  fi
done

# settings.json defaults
if node -e '
const fs=require("fs");
const p=process.argv[1];
const d=JSON.parse(fs.readFileSync(p,"utf8"));
if (d.defaultProvider!=="p8-agent") { console.error("defaultProvider="+d.defaultProvider); process.exit(1); }
if (d.defaultModel!=="kimi-k3:cloud") { console.error("defaultModel="+d.defaultModel); process.exit(1); }
if (!Array.isArray(d.enabledModels) || !d.enabledModels.includes("p8-agent/kimi-k3:cloud") || !d.enabledModels.includes("p8-agent/router/agent")) { console.error("enabledModels="+JSON.stringify(d.enabledModels)); process.exit(1); }
console.log("settings: defaultProvider=p8-agent defaultModel=kimi-k3:cloud enabledModels ok");
' "$AGENT_DIR/settings.json"; then
  :
else
  FAIL=1
fi

# models.json providers
if node -e '
const fs=require("fs");
const p=process.argv[1];
const d=JSON.parse(fs.readFileSync(p,"utf8"));
const providers = (d.providers && typeof d.providers==="object") ? Object.keys(d.providers) : [];
if (!providers.includes("p8-agent")) { console.error("missing p8-agent"); process.exit(1); }
if (!providers.includes("p8-agent-fast")) { console.error("missing p8-agent-fast"); process.exit(1); }
console.log("models: p8-agent and p8-agent-fast declared");
' "$AGENT_DIR/models.json"; then
  :
else
  FAIL=1
fi

# key.env presence only (never print value)
if grep -q '^P8_ROUTER_API_KEY=' "$AGENT_DIR/key.env" 2>/dev/null; then
  echo "key.env: P8_ROUTER_API_KEY present"
else
  echo "key.env: P8_ROUTER_API_KEY missing"
  FAIL=1
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo "pi: healthy"
  exit 0
fi
echo "pi: defect"
exit 1
