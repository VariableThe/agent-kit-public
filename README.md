# agent-kit

One repository for VariableThe's ([VariableThe](https://github.com/VariableThe))
OpenCode setup. It holds four things that used to live in three separate
repos and installs them all with one script:

- **Config** — the machine-independent `opencode.base.json` plus per-host
  overlays, merged into `~/.config/opencode/opencode.json`.
- **Global rules** — `AGENTS.md` (linked to `~/.config/opencode/AGENTS.md`)
  and `cli.json`.
- **Skills** — local-first, on-device capabilities under `skills/`.
- **Bridge** — `src/beeper-mcp-shim.mjs`, an stdio↔HTTP MCP bridge and the
  fallback route to Beeper Desktop; the primary route is remote HTTP.

It consolidates the former `agent-config`, `agent-skills`, and
`beeper-ecocsystem-wide` repos. Everything is symlinked back into place, so
this repo stays the single source of truth; edit it here, re-run the
installer, and every machine follows.

## Layout

```
agent-kit/
├── AGENTS.md               # global agent rules (-> ~/.config/opencode/AGENTS.md)
├── cli.json                # OpenCode CLI/theme config (-> ~/.config/opencode/cli.json)
├── install.sh              # the one installer (read-only by default)
├── opencode.base.json      # machine-independent OpenCode config
├── overlays/
│   ├── plain.json          # {} — default, no host-specific bits
│   └── beeper-client.json  # adds mcp.servers.beeper (remote HTTP) + beeper_ssh (ssh fallback)
├── skills/                 # 15 skills, each SKILL.md + self-contained scripts
├── src/beeper-mcp-shim.mjs # the Beeper MCP bridge (Node ≥ 22.5, zero deps)
├── test/                   # unit tests (bridge transport, tailshare + hierarchy-guard policy, e2e harness)
├── plugins/
│   ├── tailshare           # OpenCode plugin (-> ~/.config/opencode/plugins/tailshare)
│   │   ├── index.ts        # V2 plugin entry (system-hint + tool-result rewrite)
│   │   ├── rewrite.mjs     # pure helpers (unit-tested, no opencode imports)
│   │   └── package.json    # plugin manifest (host provides @opencode/plugin)
│   └── hierarchy-guard     # permission re-enforcement plugin (same wiring)
│       ├── index.ts        # V2 plugin entry (permission.evaluate deny hook)
│       ├── policy.mjs      # pure helpers (unit-tested, no opencode imports)
│       ├── README.md       # enforcement, precedence, opt-out, limitations
│       └── package.json    # plugin manifest (no deps; host provides @opencode/plugin)
├── scripts/
│   ├── check-secrets.sh
│   ├── measure-tokens.mjs  # char/token budget reporter
│   └── tailshare           # tailnet link helper (-> ~/.local/bin/tailshare)
├── requirements.txt        # Python deps for the skills
└── service.json.example    # template for local, gitignored service.json
```

## Install

```sh
git clone https://github.com/VariableThe/agent-kit.git ~/Projects/agent-kit
cd ~/Projects/agent-kit
./install.sh --check                          # read-only: report drift
./install.sh --apply --role beeper-client     # write config + links
./install.sh --check                          # should report no drift
```

`bash`, `git`, and `node` are the only requirements (node is already needed
by OpenCode). `--apply` is the only mode that writes `opencode.json`; it is
mode 600 and backed up first. Default runs (`./install.sh`, `--check`,
`--dry-run`) write nothing.

For the `beeper-client` role, export `BEEPER_MCP_TOKEN` in the environment
OpenCode/T3 Code runs under before starting it: the HTTP route
(`mcp.servers.beeper`) authenticates with that bearer token and returns 401
without it, and the `beeper_ssh` fallback then carries the work.

Role resolution is `--role` flag > `~/.config/agent-kit/host.json` > `plain`.

### What it links

| From | To |
|---|---|
| `AGENTS.md` | `~/.config/opencode/AGENTS.md` |
| `cli.json` | `~/.config/opencode/cli.json` |
| `skills/<name>` | `~/.config/opencode/skills/<name>` and `~/.agents/skills/<name>` |
| `src/beeper-mcp-shim.mjs` | `~/.local/bin/beeper-mcp-shim` |
| `scripts/tailshare` | `~/.local/bin/tailshare` |
| `plugins/tailshare` | `~/.config/opencode/plugins/tailshare` |
| `plugins/hierarchy-guard` | `~/.config/opencode/plugins/hierarchy-guard` |

The installer only creates or updates symlinks; it never overwrites a real
file or directory — it warns and skips. `./install.sh --uninstall` removes
only the symlinks it created (never `opencode.json`).

## Config: base + overlay

`opencode.base.json` is the machine-independent live config. Host-specific
settings never go in the base — they belong in an overlay selected by role.
`opencode.base.json` + `overlays/<role>.json` is deep-merged into the live
file, so adding a machine means adding an overlay, not forking the base.

Three refusal guards keep the installer safe: writing is opt-in; it refuses
to drop an `mcp.servers` entry (or top-level key) present in the live config
unless `--force`; and it refuses to install from a temporary git worktree,
because symlinks into a removed worktree dangle.

## Skills

Each skill is a `SKILL.md` (frontmatter `name`/`description`, instructions)
plus self-contained scripts. They run on-device; no file leaves the machine.

| Skill | Operation | Runtime deps |
|---|---|---|
| `pdf-toolkit` | merge, split, rotate, text watermark, page numbers | `pymupdf` |
| `pdf-convert` | images to PDF, PDF pages to PNG | `pymupdf`, `pillow` |
| `pptx-toolkit` | merge PPTX decks, slide info, slides to PDF | stdlib; `pymupdf` for to-pdf |
| `image-toolkit` | resize, crop, text watermark, box blur | `pillow` |
| `image-ocr` | image to text | `tesseract` binary |
| `image-palette` | dominant-color palette | `pillow` |
| `data-csv` | XML / Apple plist to CSV | stdlib only |
| `audio-transcribe` | speech to text (Whisper) | `faster-whisper` |
| `notes-to-pdf` | normalize NotebookLM Markdown, build HTML/PDF | `pandoc` binary |
| `nextcloud` | upload, download, read, edit, delete, share-link Nextcloud files | stdlib only |
| `beeper-collab` | keep a collaborator posted over Beeper, read replies back | stdlib only |
| `beeper-bot` | auto-reply bot on a named chat for a bounded duration | node, `ssh`, `opencode` CLI |
| `beeper` | operate Beeper Desktop across messaging networks (HTTP primary, ssh fallback) | the local forward + `BEEPER_MCP_TOKEN`; node, `ssh` for the fallback |
| `workstation` | offload heavy builds / GPU jobs to a bigger machine over SSH | `ssh`, `tailscale` |
| `tailnet-transfer` | move files across the tailnet (Taildrop, rsync/scp, HTTP serve) | stdlib only; `tailscale`, `rsync`, `tailshare` |
| `t3-thread-orchestration` | spawn, monitor, and follow up on independent top-level T3 threads | the T3 Code MCP server |

```sh
pip install -r requirements.txt        # Python deps
python3 skills/<skill>/scripts/<script>.py --help
```

`tesseract` and `pandoc` are system binaries. `beeper` needs the local
forward and `BEEPER_MCP_TOKEN`, or an SSH-reachable Beeper host for the
fallback; `workstation` expects an SSH-reachable host. See their `SKILL.md`.

## Token budget

`scripts/measure-tokens.mjs` reports chars and chars/4 tokens for AGENTS.md,
each agent system string, skill frontmatter/descriptions, and the scheduled
prompt. `test/token-budget.test.mjs` enforces the caps (AGENTS.md <= 5200,
build/monarch <= 1500, sum of systems <= 5100, skill descriptions <= 2000).
See `docs/token-budget.md`.

## Beeper: HTTP primary, SSH fallback

Beeper Desktop can run on more than one machine; each instance binds
`127.0.0.1:23373` and mints its own bearer token. The OAuth issuer is
loopback, so no other host can reach an instance directly. The overlay routes
to a Beeper host two ways:

- **`beeper` (primary, remote HTTP)** — OpenCode calls
  `http://127.0.0.1:23373/v0/mcp` with
  `Authorization: Bearer {env:BEEPER_MCP_TOKEN}` and `oauth: false` (without
  `oauth: false` OpenCode would start a browser OAuth flow). The loopback URL
  only works because of a permanent, user-managed forward on the client host,
  and the endpoint needs the bearer token — it answers 401 without it, and
  also 401 when the token belongs to a different instance. The endpoint is
  loopback-only, not a shareable URL.
- **`beeper_ssh` (fallback, ssh stdio)** — for hosts without that forward or
  token: `src/beeper-mcp-shim.mjs` runs on the Beeper host, speaks MCP over
  stdio, and forwards to the local endpoint with the stored bearer token.
  Tools appear as `beeper_ssh_*` (`tools.beeper_ssh.*`).

Both servers are active by default; to drop the ssh fallback, add
`"disabled": true` to the `beeper_ssh` entry — v2 exposes `disabled` on remote
and local MCP servers, not `enabled`.

`BEEPER_MCP_TOKEN` is exported in the environment OpenCode/T3 Code runs under
(shell profile; for the desktop app a systemd user environment such as
`~/.config/environment.d/90-beeper.conf`). It is never stored in the repo or
the live config.

```sh
npm test        # transport unit tests
npm run check   # node --check on the shim
```

Credentials live outside the repo at
`~/.config/beeper-mcp-shim/credential.json` (mode 600). The shim reads the
token from `BEEPER_MCP_TOKEN`, disk, or the OAuth flow; it never writes
secrets to stdout (stdout is the MCP channel) and never logs them.

## tailshare: tailnet-reachable links

Every link an agent shares for a local service must work from any device on
the tailnet, so `localhost` URLs are never reported as the link. `tailshare`
(a bash + node script, linked to `~/.local/bin/tailshare`) exposes a local
port with `tailscale serve` and prints the shareable URL:

```sh
tailshare url <port> [--path /name] [--port 8443|10000]  # print the URL; never mutates serve config
tailshare share <port> [--path /name] [--port 8443|10000] # expose, print ONE URL on stdout (rest to stderr)
tailshare list [--json]                                   # tailscale serve status passthrough
tailshare unshare <port|path> [--force]                   # remove only this tool's mapping
tailshare check <url>                                     # 0 = tailnet, 1 = loopback, 2 = public/other
```

It prefers path-routing under 443 (no extra port in the URL, real TLS),
falling back to `--https 8443`, then `10000`. Host, suffix, and tailnet IP
come from `tailscale status --json` at runtime (cached `~/.cache/tailshare/`,
~1h TTL) and are never hardcoded. When serve-mapping is impossible it prints
`http://<tailnet-ip>:<port>` and says so; when tailscale itself is
unavailable it prints `http://127.0.0.1:<port>` with a stderr caveat and
exits 2.

Tailnet-only: it never enables funnel and never touches the existing 443
root `/` mapping (removing `/` requires explicit `--force`).

## tailshare plugin (OpenCode tier)

`plugins/tailshare` is the OpenCode plugin tier of the same feature, built
only on mechanisms the installed runtime supports (verified against the
`opencode v2.0.29` binary — not the stale V1 `@opencode-ai/plugin` types,
whose hooks the binary never calls):

- **System-prompt hint.** A `session.hook("context")` (plus `"compaction"`
  so the guidance survives summaries) appends one short text block: expose
  the port via `tailshare share <port>` and share the printed URL, never
  report loopback URLs as the link. Idempotent, one to two lines (it rides
  every model call).
- **Tool-result rewrite.** A `tool.hook("execute.after")` rewrites loopback
  URLs (`localhost`, `127/8`, `0.0.0.0`, `::1`) in **shell tool results
  only** (an allowlist, so `read`/`write`/`edit`/`patch` file content is
  never touched). A tailnet URL is emitted **only** when a real, live
  `tailscale serve` mapping for that port exists (parsed from
  `tailscale serve status --json`, cached 60s in plugin storage); otherwise
  the URL is left intact with a `(local-only; run: tailshare share <port>)`
  annotation — never a fabricated URL.

Free-form assistant prose has no rewrite hook in v2, so it is out of reach
for the plugin; the `AGENTS.md` rule plus the hint cover it.

**Opt-out:** `TAILSHARE_OFF=1` disables the plugin entirely.

**Fail-safe:** every hook is try/catch-guarded and setup never throws. When
`tailscale` is missing or serve state is unreadable, the plugin registers
nothing observable and sessions behave exactly as before.

**Install wiring:** the installer symlinks the whole directory to
`~/.config/opencode/plugins/tailshare`, which OpenCode auto-discovers — no
config change. `package.json` declares `@opencode/plugin 2.0.29` (the exact
installed release) for types, but its `node_modules` must **not** be
installed inside the plugin directory: the host runtime provides that
module. Tests live at `test/tailshare-rewrite.test.mjs` and run with the
repo's usual `npm test`.

## hierarchy enforcement (plugin)

Agent `permissions` in `opencode.base.json` are not self-enforcing: client
layers (T3 Code, an SDK `session.update({ permissions })`) append
session-level rules that win under the runtime's last-match-wins
resolution, which is how read-only agents were observed executing
shell/edit calls. `plugins/hierarchy-guard` closes that gap: a
`permission.hook("evaluate")` re-checks the session's agent against its
configured policy after client rules resolve and forces `deny` when the
policy denies. It runs after client rules and can only tighten — never
`deny`/`ask` to `allow`. The permission-less `execute` (Code Mode JS) tool,
whose sandbox exposes `child_process`, is gated separately: for agents whose
policy denies shell, its input is replaced with an inert payload (verified:
the tool then errors with `Code cannot be empty` and runs nothing).

**Opt-out:** `HIERARCHY_GUARD_OFF=1`. **Fail-safe:** missing/malformed
config or unexpected events leave the session untouched; setup never throws.

**Subagent model routing.** The native OpenCode `subagent` tool picks its child
model as `input.model ?? agent.model ?? parentSession.model`, so an omitted
model inherits the parent's paid model. The plugin listens on
`tool.hook("execute.before")` for that tool and pins bishop/knight inputs to
their configured free model read from the live config — a corrective pin, not a
deny (the call proceeds on the pinned model). This is mechanical, but only for
the native tool.

**Known limitation:** T3 Code's app-owned tools do not fire the OpenCode
`tool.hook("execute.before")` event (they are not in the opencode binary), so
agent-kit cannot hard-enforce their child model:
- native OpenCode `subagent`: explicit `agent` + configured `model`, mechanically
  pinned by the plugin;
- T3 `functions.subagent`: explicit `agent` + exact configured `model`,
  caller/prompt-enforced only;
- T3 `delegate_task`: explicit `target.providerInstanceId` + `target.model`,
  caller/prompt-enforced only.
The build/monarch prompts and `AGENTS.md` require the caller to pass those
explicit values; only T3's own dispatcher can enforce them server-side.
Enforcement elsewhere applies wherever the session's agent identity matches a
configured agent — hence the build/monarch prompts instruct: when delegating via
the native subagent tool, pass the intended worker as the tool's `agent`
parameter (`bishop` planning, `knight` implementation, `mercenary` free
fallback).

Details in `plugins/hierarchy-guard/README.md`; unit tests at
`test/hierarchy-guard-policy.test.mjs` (`npm test`); live-binary harness at
`test/hierarchy-guard-e2e.sh` (needs network + `opencode`, not in `npm test`).

## Secrets policy

Never commit secrets. Local secrets stay out of the repo:

- `service.json` (from `service.json.example`, gitignored),
- `~/.local/share/opencode/auth.json` and other `auth.json` files,
- the Beeper token at `~/.config/beeper-mcp-shim/credential.json`.

`bash scripts/check-secrets.sh` scans tracked files for token-shaped
patterns and exits non-zero on a hit.

## Git workflow

Feature branches, Conventional Commits, PRs only — never direct to `main`.
Run `npm test`, `npm run check`, and `bash scripts/check-secrets.sh` before
opening a PR.

## License

MIT — see `LICENSE`. The `beeper-mcp-shim.mjs` bridge is part of this repo.
