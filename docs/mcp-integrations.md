# MCP Server Integration Candidates

Findings for three candidate MCP servers evaluated for the agent-kit repo
(Figma, DaVinci Resolve, GitHub). Every claim is verified against the page or
package metadata fetched during research; URLs are cited inline. Where a fact
could not be verified, it is marked **unverified**.

Repo config schema (from `opencode.base.json`): servers live under
`mcp.servers.<name>` with `"type": "remote"` + `"url"` for HTTP, or
`"type": "local"` + a single `"command"` array (executable + args) for stdio.
Local servers carry env vars under `environment` (not `env`), remote servers
carry auth under `headers` (e.g. `"Authorization": "Bearer {env:VAR}"`) and an
`oauth` flag. Host-specific servers belong in `overlays/<role>.json`, never in
`opencode.base.json` (see `overlays/beeper-client.json` and `install.sh`).

---

## 1. GitHub MCP

**Availability: available now, production-grade, official.**

Two official deployments, plus one community server that is now deprecated.

### 1.1 Official `github/github-mcp-server`

- Repo: https://github.com/github/github-mcp-server (33,903 stars, last push
  2026-10-23 — actively maintained).
- License: **MIT** (verified `LICENSE`, copyright 2025 GitHub).
- Transports:
  - **Remote (hosted HTTP, Streamable HTTP)**: `https://api.githubcopilot.com/mcp/`.
    Auth via OAuth (browser flow) or a PAT in the `Authorization` header.
    Read-only variant via URL suffix `/readonly`; insiders via `/insiders`.
  - **Local (stdio)** via Docker image `ghcr.io/github/github-mcp-server`
    (`docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server`),
    or a `go build`-produced `github-mcp-server stdio` binary.
- Auth (env vars): `GITHUB_PERSONAL_ACCESS_TOKEN` (PAT, takes precedence over
  OAuth). Local Docker OAuth uses a fixed loopback callback port
  `GITHUB_OAUTH_CALLBACK_PORT` (default 8085); the token is kept in memory only.
- Toolset config — the `toolsets` concept. When none are set, the **default
  toolsets** are `context, repos, issues, pull_requests, users`. The full list
  includes `actions, code_quality, code_security, copilot, dependabot,
  discussions, gists, git, governance, issues, labels, notifications, orgs,
  projects, pull_requests, repos, secret_protection, security_advisories,
  stargazers, users, context`, plus remote-only `copilot` extras,
  `copilot_spaces`, `github_support_docs_search`. Selection is via
  `--toolsets` flag or `GITHUB_TOOLSETS` env var (comma list; `all` and
  `default` are special values), individual tools via `--tools`/`GITHUB_TOOLS`.
- Verified env vars (from the README and `docs/server-configuration.md`):
  `GITHUB_PERSONAL_ACCESS_TOKEN`, `GITHUB_TOOLSETS`, `GITHUB_TOOLS`,
  `GITHUB_READ_ONLY` (set to `1` to strip write tools), `GITHUB_HOST`
  (GHES/ghe.com), `GITHUB_INSIDERS`, `GITHUB_LOCKDOWN_MODE`,
  `GITHUB_OAUTH_CALLBACK_PORT`, `GITHUB_MCP_*` (tool description overrides).
- `GITHUB_DYNAMIC_TOOLSETS` is **not present** in the current README or
  server-configuration docs (zero matches across the fetched files). It appears
  to be a legacy/third-party name; treat it as **unverified** and do not rely on
  it.
- Tool surface: ~100+ tools across the toolsets (repos/PRs/issues/actions/code
  security/dependabot/etc.), listed in the README's auto-generated "Tools"
  section.

### 1.2 Community `modelcontextprotocol/servers` git/github

- The **`github`** reference server has been **removed** from the active
  `modelcontextprotocol/servers` repo (its `src/` no longer lists `github`) and
  now lives in the archived `modelcontextprotocol/servers-archived` repo
  (`src/github`). It is superseded by the official server; do not adopt it.
- The **`git`** server remains active at
  https://github.com/modelcontextprotocol/servers/tree/main/src/git (local stdio,
  Python, no credentials — it shells out to the local `git`). Tools: `git_status`,
  `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_commit`, `git_add`,
  `git_reset`, `git_log`, `git_create_branch`, and more. License is in an
  MIT→Apache-2.0 transition (see the repo `LICENSE`).

### 1.3 Config snippet (this repo's schema)

Remote, OAuth (no secret to store — recommended):

```json
"github": {
  "type": "remote",
  "url": "https://api.githubcopilot.com/mcp/"
}
```

Remote, PAT for headless/automation (real value lives in the environment, not git):

```json
"github": {
  "type": "remote",
  "url": "https://api.githubcopilot.com/mcp/",
  "oauth": false,
  "headers": {
    "Authorization": "Bearer {env:GITHUB_PERSONAL_ACCESS_TOKEN}"
  }
}
```

Local Docker, PAT (equivalent, stdio):

```json
"github": {
  "type": "local",
  "command": ["docker", "run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"],
  "environment": {
    "GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_PERSONAL_ACCESS_TOKEN}"
  }
}
```

The token `GITHUB_PERSONAL_ACCESS_TOKEN` must be exported in the environment
OpenCode/T3 runs under, never committed. `gh auth status` on the test host is
already logged in (scopes `gist, read:org, repo, workflow`), so a PAT can be
minted to match.

**Recommendation: add now.** Official, MIT, no desktop app dependency, huge
surface (repos/PRs/issues/actions), and both OAuth (no secret) and PAT paths
work on the test host (`docker` present, `gh` authenticated). Main risk is
context bloat from the large tool set — mitigate by pinning toolsets (e.g.
`GITHUB_TOOLSETS=context,repos,issues,pull_requests`) or adding read-only mode.

---

## 2. Figma MCP

**Availability: official server is usable but Beta, seat/rate gated, and
proprietary. Community npm package `mcp-server-figma` is dead.**

### 2.1 Official Figma MCP server (Beta)

- Docs: https://developers.figma.com/docs/figma-mcp-server/ (guide repo
  https://github.com/figma/mcp-server-guide, 2,107 stars; no open-source
  license — proprietary service).
- Two connection modes:
  - **Remote (recommended)**: Streamable HTTP at `https://mcp.figma.com/mcp`,
    OAuth browser auth. Broadest feature set, including write-to-canvas.
  - **Desktop (local)**: runs inside the **Figma desktop app**, exposed at
    `http://127.0.0.1:3845/mcp`. Requires the desktop app running and a Dev or
    Full seat on a paid plan; **read-only** (no write-to-canvas). No separate
    token — auth rides on the desktop app's session.
- Tool surface (verified from `tools-and-prompts`):
  - Read: `get_design_context`, `get_metadata`, `get_screenshot`,
    `get_variable_defs`, `get_code_connect_map`, `get_motion_context`,
    `get_figjam`, `get_libraries`, `whoami`, `download_assets`,
    `search_design_system`, and generative-plugin/shader readers.
  - Write (remote only): `use_figma`, `create_new_file`, `upload_assets`,
    `generate_image`, `generate_diagram`, `html_to_figma`,
    `send_code_to_figma`, `add_code_connect_map`, and generative-plugin/shader
    writers.
  - One MCP prompt: `create_design_system_rules`.
- Rate limits / access: Starter plan and View/Collab seats are limited to **6
  tool calls per month**; Dev/Full seats follow Tier-1 REST API limits. Beta;
  write-to-canvas will eventually become usage-based paid.
- The official server ships **no npm package** — `@figma/figma-developer-mcp`
  returns 404 from the npm registry (unverified/does not exist).

### 2.2 Community packages

- `mcp-server-figma` (npm): the package is now a **security holding package**
  (`0.0.1-security`), i.e. the original was removed from npm. Do not use.
- `figma-mcp` (npm, from https://github.com/MatthewDailey/figma-mcp): stdio
  server (`npx figma-mcp`) over the Figma REST API, reads files and reads/posts
  comments only. Auth via `FIGMA_API_KEY` env var. 213 stars, last push
  2025-05-29 (stale), **no license file**. Narrow surface; the official server
  supersedes it.

### 2.3 Config snippets (this repo's schema)

Remote (OAuth — no secret):

```json
"figma": {
  "type": "remote",
  "url": "https://mcp.figma.com/mcp"
}
```

Desktop (requires the Figma desktop app open and the server enabled in Dev Mode):

```json
"figma-desktop": {
  "type": "remote",
  "url": "http://127.0.0.1:3845/mcp"
}
```

A community REST-API server would use `"type": "local"` with
`"command": ["npx", "figma-mcp"]` and `"environment": { "FIGMA_API_KEY": "{env:FIGMA_API_KEY}" }`
— but this is not recommended over the official server.

**Host prerequisite:** the Figma **desktop app is installed** on the test host
(`/opt/figma-linux-next`, binary `/usr/bin/figma-linux-next`, config
`~/.config/figma-linux-next` present), but `command -v figma` is absent (the
binary is `figma-linux-next`, not `figma`).

**Recommendation: add behind an overlay.** It is genuinely useful and the
desktop app is already present, but it is Beta, proprietary, seat-gated (the
desktop server needs a paid Dev/Full seat; the remote server is rate-limited on
free tiers), and auth is per-user (OAuth). That makes it host/user-specific, so
it belongs in an overlay (like `beeper-client`) rather than the base config.

---

## 3. DaVinci Resolve MCP

**Availability: community servers exist but are Studio-gated; no Resolve on this
machine; official server unverified.**

### 3.1 Canonical community server `samuelgursky/davinci-resolve-mcp`

- Repo: https://github.com/samuelgursky/davinci-resolve-mcp (3,723 stars, last
  push 2026-10-23; npm `davinci-resolve-mcp`, v4.15.3; **MIT**).
- Drives Resolve through the official **Scripting API**, so it requires
  **DaVinci Resolve Studio** (the paid edition) 18.5+ with
  `Preferences > General > External scripting using = Local`, and Python 3.10+.
  Launch: `npx davinci-resolve-mcp setup`, or a source install via `python
  install.py`.
- Tool surface: 37 "compound" tools (default) or 390 granular
  one-tool-per-API-method tools (`--full`), covering editing, media pool,
  render, grading, Fusion, Fairlight, markers, project lifecycle; plus an
  optional Node "advanced" server (18 tools) that edits `.drp`/`.drt`/`.drx`
  files offline.
- Free-edition path: an "in-app bridge" (a script launched from Resolve's
  Scripts menu re-exports the live `resolve` object over a loopback listener).
  The README explicitly warns this is fragile and that **Resolve 21.1 moved
  Python scripting to Studio**, so the bridge is effectively a 21.0.x-only path.

### 3.2 Official Resolve MCP (unverified)

A community HOWTO repo
(https://github.com/theRealAi/davinci-resolve-mcp-over-lan, 0 stars) claims
DaVinci Resolve **21.1+ ships an official stdio server at
`C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`** (Windows
only, no network transport). This is a single low-signal source and could not
be confirmed against Blackmagic's official developer docs — mark **unverified**.

### 3.3 Many other community repos

`gh search repos "davinci resolve mcp"` returns dozens of forks/variants
(e.g. 44-tool, 53-tool, 334-tool, "free edition" FCPXML/Lua bridges). None
exceeds `samuelgursky` in stars or maturity; all share the Studio-scripting
requirement for live control.

### 3.4 Host prerequisites

`command -v resolve` is **absent** and no DaVinci Resolve installation was found
(`/opt`, `/usr/lib`, app menus all clean). Nothing to connect a server to.

**Recommendation: defer.** No Resolve is installed on the test host, live
control requires the paid Studio edition's scripting API, the free-edition
bridge is explicitly fragile and version-gated, and the purported official
`ResolveMCP.exe` is unverified. Revisit only if/when Resolve Studio is installed
on a host and there is a concrete editing-automation need. (No config snippet:
there is nothing to wire up locally.)

---

## Host prerequisites (test host)

| Check | Command | Result |
| --- | --- | --- |
| `figma` on PATH | `command -v figma` | absent (binary is `figma-linux-next`) |
| Figma desktop app | `ls /opt/figma-linux-next` | **present** |
| Figma config dir | `ls ~/.config/figma-linux-next` | present |
| `resolve` on PATH | `command -v resolve` | absent |
| DaVinci Resolve installed | filesystem scan | **absent** |
| Docker | `command -v docker` | present (`/usr/bin/docker`) |
| GitHub CLI auth | `gh auth status` | logged in (scopes `gist, read:org, repo, workflow`) |
| `bun` | `command -v bun` | present (`/home/<user>/.bun/bin/bun`) |
| `npx` | `command -v npx` | present (`/usr/bin/npx`) |

---

## Summary comparison

| Candidate | Verdict | License | Transport | Auth | Desktop app required | Host ready? |
| --- | --- | --- | --- | --- | --- | --- |
| GitHub (official) | **add now** | MIT | remote HTTP + stdio (Docker) | OAuth or PAT | no | yes (docker, gh) |
| GitHub (community git) | optional, local only | MIT→Apache-2.0 | stdio | none | no | yes |
| Figma (official) | **add behind overlay** | proprietary (Beta) | remote HTTP + local HTTP | OAuth (or app session) | desktop server yes | app present, seat unverified |
| Figma (community) | skip | none (`figma-mcp`) / dead (`mcp-server-figma`) | stdio | `FIGMA_API_KEY` | no | n/a |
| DaVinci Resolve | **defer** | MIT (community) / unverified (official) | stdio | none (local scripting) | yes (Studio) | no Resolve installed |

## Recommended next steps

1. **Add GitHub MCP now** to `opencode.base.json` as the remote OAuth server
   (`https://api.githubcopilot.com/mcp/`) — no secret required, works on every
   host. Optionally pin toolsets to keep context lean.
2. **Document the PAT path** for headless automation: reference
   `{env:GITHUB_PERSONAL_ACCESS_TOKEN}` and note the token must be exported in
   the environment, never committed (it is not covered by `.gitignore`, which
   only ignores `service.json`, `auth.json`, `pi/key.env`).
3. **Add Figma behind an overlay** (a new `overlays/figma-client.json`), not the
   base: wire the remote server (OAuth) and/or the desktop server
   (`http://127.0.0.1:3845/mcp`, since the desktop app is already installed),
   and confirm the account has a Dev/Full seat before promising write-to-canvas.
4. **Defer DaVinci Resolve** until Resolve Studio is installed somewhere and a
   concrete editing-automation need exists; then evaluate
   `samuelgursky/davinci-resolve-mcp` (MIT) and re-verify the purported official
   `ResolveMCP.exe`.
5. If a local-git-only capability is wanted without GitHub API scope, consider
   the community `modelcontextprotocol/servers` `git` server as a low-risk
   stdio addition (no credentials).

### Evidence fetched (URLs)

- https://raw.githubusercontent.com/github/github-mcp-server/main/README.md
- https://raw.githubusercontent.com/github/github-mcp-server/main/docs/server-configuration.md
- https://raw.githubusercontent.com/github/github-mcp-server/main/docs/remote-server.md
- https://raw.githubusercontent.com/github/github-mcp-server/main/docs/installation-guides/install-opencode.md
- https://raw.githubusercontent.com/github/github-mcp-server/main/LICENSE
- https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/git/README.md
- https://api.github.com/repos/modelcontextprotocol/servers/contents/src
- https://api.github.com/repos/modelcontextprotocol/servers-archived/contents/src
- https://raw.githubusercontent.com/modelcontextprotocol/servers/main/LICENSE
- https://developers.figma.com/docs/figma-mcp-server/
- https://developers.figma.com/docs/figma-mcp-server/local-server-installation/
- https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/
- https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/
- https://raw.githubusercontent.com/figma/mcp-server-guide/main/README.md
- https://registry.npmjs.org/mcp-server-figma , https://registry.npmjs.org/figma-mcp , https://registry.npmjs.org/@figma/figma-developer-mcp
- https://raw.githubusercontent.com/MatthewDailey/figma-mcp/main/README.md
- https://raw.githubusercontent.com/samuelgursky/davinci-resolve-mcp/main/README.md
- https://raw.githubusercontent.com/theRealAi/davinci-resolve-mcp-over-lan/main/README.md
