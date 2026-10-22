# Pi harness config for P8

Pi (`@earendil-works/pi-coding-agent`, pinned to 1.3.0) is the terminal coding
agent harness used alongside OpenCode. This directory is the single source of
truth for its P8 configuration and installer:

- `models.json` — providers `p8-agent` (`https://ollama.ecitis.org/agent/v1`)
  and `p8-agent-fast` (`/agent-fast/v1`); the key is referenced only as
  `$P8_ROUTER_API_KEY`, never stored inline.
- `settings.json` — defaults: provider `p8-agent`, model `kimi-k3:cloud`.
- `p8-launch.py` — launcher; reads `key.env` (never shell code), injects the
  key into the Pi child process, sets `PI_CODING_AGENT_DIR`.
- `install.py` / `install.sh` / `install.ps1` — installer (stdlib Python only).
- `refresh-models.py` — refresh model lists from live router discovery.
- `package.json` / `bun.lock` — Pi 1.3.0 dependency pin for the CLI install.
- `model-metadata.json` — saved exact-model metadata used at install/refresh.
- `manifest.json` — bundle checksums for every shipped file except the secret.
- `key.env.example` — placeholder for the secret bootstrap. The real key is
  never committed.

Prerequisites: `python3` 3.10+ or `uv`, plus `bun` for the CLI install.

## Install (macOS / Linux)

1. Bootstrap the secret. Create `~/.pi/agent/key.env` from the example, put
   your key in it, and lock it down:

   ```sh
   mkdir -p ~/.pi/agent
   cp key.env.example ~/.pi/agent/key.env
   $EDITOR ~/.pi/agent/key.env   # P8_ROUTER_API_KEY=<your key>
   chmod 600 ~/.pi/agent/key.env
   ```

   The value after `=` must match `[A-Za-z0-9_.-]+` (the launcher rejects
   anything else). `scripts/bootstrap.sh` at the repo root does this step for
   you — it can seed from `~/.config/opencode/secrets/p8-api-key` or prompt.

2. Install or refresh the config and CLI:

   ```sh
   sh ./pi/install.sh --make-default
   ```

   The installer verifies `manifest.json`, merges providers into an existing
   `models.json` (unrelated providers and existing auth are preserved), writes
   `settings.json`/`p8-launch.py` with private backups, and runs
   `bun install --frozen-lockfile` under `~/.local/share/p8-pi/pi-1.3.0`,
   creating the launcher at `~/.local/bin/pi`. A missing network falls back to
   `--offline` (ships the bundled `models.json` snapshot). Re-running is safe.

   `--make-default` sets the default to `p8-agent/router/agent`; to keep
   `kimi-k3:cloud` as the default (this repo's convention), re-apply it
   afterwards — `scripts/bootstrap.sh` does this automatically.

Windows: `.\pi\install.ps1 --make-default` (Bun, Python or uv, Git for Windows
for Pi's Bash tool).

## PATH note

The launcher is `~/.local/bin/pi`. A bare `pi` can resolve to an unrelated
`~/.bun/bin/pi` (or another `pi` earlier on PATH), so put `~/.local/bin` first
or invoke `~/.local/bin/pi` explicitly. The launcher supplies the key only to
the Pi child process; shell profiles are not modified.

## Refresh

```sh
python3 pi/refresh-models.py refresh      # or: uv run --no-project python pi/refresh-models.py refresh
```

Discovery is unauthenticated; existing auth in the installed `models.json` is
preserved. Use `--include-explore` to opt into the unqualified chat/explore
catalog.

## Verify

```sh
python3 pi/install.py verify --bundle pi
```

checks `manifest.json` checksums for every shipped file. A `key.env` placed in
this directory is never required and never belongs in git; keep the key only at
`~/.pi/agent/key.env` (mode 600).

Health check after install: `bash scripts/check-pi.sh` (exit 0 healthy).
