# Pi configuration for P8 work

Pi (`pi-coding-agent` 1.3.0) is the preferred harness for P8 work. Its config,
installer, and launch scripts now live **in this repo at `pi/`** — that
directory is the single source of truth (previously an extracted bundle under
`/tmp`). No secret ships in the repo: `models.json` references
`$P8_ROUTER_API_KEY` and the real key exists only at
`~/.pi/agent/key.env` (mode 0600).

Installed layout per host:

- Config dir: `~/.pi/agent/` (`models.json`, `settings.json`, `key.env`,
  `p8-launch.py`); CLI under `~/.local/share/p8-pi/pi-1.3.0/`, launcher on
  `PATH` at `~/.local/bin/pi` (`~/.pi/agent/p8-launch.py` injects the key from
  the private `key.env` into the child process; the file is parsed, never
  executed as shell).
- Providers: `p8-agent` (reliable) and `p8-agent-fast`, from the P8 router
  base URLs in `models.json`.
- Default model: `kimi-k3:cloud` on `p8-agent` — `defaultProvider:
  "p8-agent"`, `defaultModel: "kimi-k3:cloud"` in `settings.json`, with
  `enabledModels` containing `p8-agent/kimi-k3:cloud` and
  `p8-agent/router/agent` (the bare `kimi-k3` is not in the qualified list).
- PATH: a bare `pi` may resolve to an unrelated `~/.bun/bin/pi`; keep
  `~/.local/bin` earlier on PATH or invoke `~/.local/bin/pi` explicitly.

## Install / bootstrap flow

Fresh device, one shot:

```sh
bash scripts/bootstrap.sh            # opencode --apply + key.env + Pi + health check
```

Manual equivalent:

1. `cp pi/key.env.example ~/.pi/agent/key.env`, paste the key, `chmod 600`
   (the value must match `[A-Za-z0-9_.-]+`; the launcher regex rejects
   anything else).
2. `sh ./pi/install.sh --make-default` — verifies `pi/manifest.json`
   checksums, merges providers (existing auth and unrelated providers are
   preserved), installs the pinned CLI via `bun install --frozen-lockfile`,
   creates `~/.local/bin/pi`. Live discovery regenerates the catalog;
   `--offline` uses the bundled `models.json` snapshot.
3. `--make-default` sets `defaultModel` to `router/agent`; re-apply
   `kimi-k3:cloud` afterwards (bootstrap does this for you).

The repo copy of `pi/install.py` differs from the upstream private bundle in
exactly one way: a distribution bundle ships no `key.env`, so bundle
verification skips the `key.env` checksum when the file is absent and the
installer falls back to the config directory's established private
`key.env`. Behaviour is unchanged when a private bundle does carry `key.env`.

Refresh the model lists later with
`python3 pi/refresh-models.py refresh` (unauthenticated discovery; existing
auth is preserved). Re-verify the bundle any time with
`python3 pi/install.py verify --bundle pi`.

The OpenCode secret at `~/.config/opencode/secrets/p8-api-key` is the same
recipient key in bare form (no prefix); if `key.env` ever needs rebuilding,
prepend `P8_ROUTER_API_KEY=` — `scripts/bootstrap.sh` does this. Never print
key values; never commit `key.env` (gitignored as `pi/key.env`).

## Fleet status (2026-10-22)

Pi is on every system: this host is verified; your-host is asserted by the
user but unverified from here. After bootstrap, defaults are
`defaultProvider: "p8-agent"`, `defaultModel: "kimi-k3:cloud"`,
`enabledModels: ["p8-agent/kimi-k3:cloud", "p8-agent/router/agent"]`.

Health check: `scripts/check-pi.sh` (read-only; exit 0 healthy, 1 defect,
2 not installed).
