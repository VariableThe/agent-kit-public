# agent-kit

A portable agent setup you can install into your own home directory: global
rules, a skills library, two OpenCode plugins, an opt-in installer, and offline
checks that keep the tree honest.

Everything here is files on disk. Nothing contacts a service, mints a token, or
writes to your config until you run something yourself.

## Layout

```
AGENTS.md                 global rules, installed as ~/.config/opencode/AGENTS.md
install.sh                opt-in installer (--check / --apply / --uninstall)
docs/                     hierarchy notes, MCP integration notes, token budget
plugins/hierarchy-guard/  model-routing and delegation-depth guard for OpenCode
plugins/tailshare/        rewrites loopback URLs in replies to shareable ones
scripts/                  token measurement, revert scripts, release/secret checks
skills/                   the skill library (one directory per skill)
test/                     offline tests for the policy, revert script, plugins
```

## Install

Requirements: `git` and `bash`. Optional: Node 20+ (plugins, tests, token
budget), Python 3.9+ (a handful of skills).

```bash
git clone https://github.com/VariableThe/agent-kit-public.git ~/agent-kit
cd ~/agent-kit
./install.sh --check      # report drift; changes nothing
./install.sh --apply      # create the symlinks
./install.sh --uninstall  # remove them again
```

What `--apply` links into `~/.config/opencode/`:

| Item | Destination |
| --- | --- |
| `AGENTS.md` | `~/.config/opencode/AGENTS.md` |
| each `skills/<name>/` | `~/.config/opencode/skills/<name>` |
| `plugins/hierarchy-guard/` | `~/.config/opencode/plugins/hierarchy-guard` |
| `plugins/tailshare/` | `~/.config/opencode/plugins/tailshare` |

Skills are also linked into `~/.agents/skills/` (created if missing).

The installer is deliberately conservative:

- Nothing is written without `--apply`.
- It refuses to touch an existing `~/.config/opencode/opencode.json` that looks
  live, rather than merging into it.
- It refuses to run from a temporary directory unless `--force` is given, so a
  stray copy under `/tmp` cannot take over your config.
- **Config management is optional and off by default.** This public repo ships
  no `opencode.base.json` and no `overlays/`, so the installer links rules,
  skills and plugins and reports `config: not managed by this repo`. Your
  `opencode.json` is left alone. `--role` needs those overlay files and will
  say so instead of guessing.

## Skills

36 skills. Each directory is one skill with a `SKILL.md` carrying `name`,
`description` and the body of instructions; some ship scripts, references or
assets.

| Skill | What it does | Origin |
| --- | --- | --- |
| `academy-guide` | Points how-to-use-Claude questions at matching Claude Academy courses and tutorials. | upstream |
| `algorithmic-art` | Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration. | upstream |
| `audio-transcribe` | Transcribe audio locally with Whisper. | repo |
| `beeper` | Send/read/search Beeper chats across networks. | repo |
| `beeper-bot` | Run an auto-reply bot on a Beeper chat for a bounded time. | repo |
| `beeper-collab` | Keep a collaborator posted over Beeper and read their replies. | repo |
| `brand-guidelines` | Applies Anthropic's official brand colors and typography to artifacts. | upstream |
| `caldav` | Read and write events on a self-hosted CalDAV calendar (Nextcloud). | repo |
| `claude-api` | Reference for the Claude API and Anthropic SDK: models, pricing, params, streaming, tool use, MCP, caching, migrations. | upstream |
| `data-csv` | Convert XML/plist to CSV locally. | repo |
| `discernment-nudge` | Appends 2-3 specific follow-up questions so you can check facts, reasoning and missing context. | upstream |
| `frontend-design` | Guidance for distinctive, intentional visual design when building or reshaping UI. | upstream |
| `google-calendar` | Read and manage Google Calendar events (list, agenda, free/busy, create, update, delete). | repo |
| `image-ocr` | OCR images to text with Tesseract. | repo |
| `image-palette` | Extract dominant colors as hex. | repo |
| `image-toolkit` | Resize/crop/watermark/blur images with Pillow. | repo |
| `internal-comms` | Formats for internal communications: status reports, 3P updates, newsletters, FAQs, incident reports. | upstream |
| `mcp-builder` | Guide for building high-quality MCP servers, with Python and Node reference implementations. | upstream |
| `nextcloud` | Upload, download, read, edit, delete, and share-link files on Nextcloud. | repo |
| `notes-to-pdf` | Normalize notes to Markdown/HTML/PDF. | repo |
| `pdf-convert` | Convert images and PDF pages. | repo |
| `pdf-toolkit` | Merge/split/rotate/watermark/page-number PDFs. | repo |
| `pptx-toolkit` | Merge/inspect/export PPTX decks. | repo |
| `report` | Generate structured reports (Markdown to DOCX) with a draft-review-render workflow. | repo |
| `skill-creator` | Create new skills, improve existing ones, and measure skill performance. | upstream |
| `slack-gif-creator` | Utilities for creating animated GIFs optimized for Slack. | upstream |
| `slide-deck` | Generate self-contained HTML slide decks from a Markdown outline, with themes and speaker notes. | repo |
| `t3-thread-orchestration` | Launch, monitor and follow up agent threads. | repo |
| `tailnet-transfer` | Move files across a tailnet. | repo |
| `theme-factory` | Style artifacts with one of ten preset themes (colors and fonts), or generate a new theme. | upstream |
| `thread-todo` | Persistent per-thread todo list on disk, so an interrupted thread resumes from the list. | repo |
| `todoist` | Manage Todoist projects, labels, sections and tasks. | repo |
| `unslop` | Remove AI writing patterns from prose. | upstream |
| `web-artifacts-builder` | Tools for building multi-component HTML artifacts with modern frontend tooling. | upstream |
| `webapp-testing` | Interact with and test local web applications using Playwright. | upstream |
| `workstation` | Offload heavy builds, tests and GPU jobs to a more powerful SSH host. | repo |

The Origin column is this table's own annotation, not a skill frontmatter
field. `upstream` means the skill is vendored from a third-party repository
under its own license; see [docs/provenance.md](docs/provenance.md) for the
per-skill source and license. `repo` means it was written here.

Skills marked upstream keep their original `LICENSE.txt` or `LICENSE` next to
the skill. Do not strip those files when copying a skill elsewhere.

## Plugins

**`hierarchy-guard`** — an OpenCode plugin that reads the permission and
subagent calls and enforces two policies: which models a given agent may run
on, and how deep delegation may go. It fails closed: an unknown agent, an
unresolvable model, or a dispatch whose target cannot be verified is blocked
rather than allowed. See [plugins/hierarchy-guard/README.md](plugins/hierarchy-guard/README.md).

**`tailshare`** — rewrites loopback URLs (`localhost`, `127.0.0.1`, `0.0.0.0`,
`::1`) in a reply so you do not hand someone an address only you can reach. It
expects a `tailshare` CLI on `PATH`; this repo does not ship that CLI, and
without it the plugin reports the URL as local instead of inventing a public
one.

Both plugins are plain ESM modules; `npm run check` syntax-checks them.

## Docs

| File | Contents |
| --- | --- |
| [AGENTS.md](AGENTS.md) | The global rules themselves: communication, verification, delegation depth, git workflow, escalation. |
| [docs/hierarchy-model.md](docs/hierarchy-model.md) | How the planning/implementation/review agents are meant to divide work, and what the guard can and cannot enforce. |
| [docs/mcp-integrations.md](docs/mcp-integrations.md) | Notes on wiring MCP servers (remote and local) and the trade-offs of each. |
| [docs/token-budget.md](docs/token-budget.md) | What costs context, the current measured numbers, and the enforced budgets. |
| [docs/provenance.md](docs/provenance.md) | Where each vendored skill came from, and what is deliberately not published here. |

## Checks

These run offline. Nothing installs a dependency; Node and Python are used only
if present.

```bash
npm run check          # syntax-check plugins and scripts
npm test               # plugin and policy tests (node --test)
npm run test:python    # python tests inside skills/
npm run test:revert    # revert script against a throwaway HOME
npm run test:notice    # bundled shadcn/ui notice, with a stub pnpm
npm run budget         # re-measure the token budget
scripts/check-secrets.sh          # credential-shaped strings
scripts/check-public-release.sh   # private-context patterns before publishing
```

`test/revert-hierarchy.test.sh` builds a throwaway `HOME` under `mktemp -d` and
never touches your real config.

One check is opt-in and not offline: `npm run test:e2e` drives the real
`opencode` binary against a free model in a stripped `HOME` under
`/tmp/opencode/hg-e2e`, so it needs `opencode` on `PATH` and network access. It
is not part of `npm test`.

## Revert

`scripts/revert_hierarchy.sh` (bash) and `scripts/revert_hierarchy.ps1`
(PowerShell) undo the installer and the hierarchy agent entries: they remove
the hierarchy agents and the `build`/`plan` overrides from `opencode.json`,
then remove the plugin folder and the symlinks this repo's installer created.
Providers, models and credentials are left exactly as they were. Both write a
timestamped backup before rewriting a config, and both accept a dry run.

```bash
curl -fsSL https://raw.githubusercontent.com/VariableThe/agent-kit-public/main/scripts/revert_hierarchy.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/VariableThe/agent-kit-public/main/scripts/revert_hierarchy.ps1 | iex
```

To clean a project-local `opencode.json` as well, pass the project path as the
first argument (bash: `scripts/revert_hierarchy.sh ~/code/my-project`).

## Secrets and local state

- No credentials, tokens, account ids or machine paths belong in this repo.
- `scripts/check-secrets.sh` scans tracked files for credential-shaped strings
  and prints redacted matches only.
- `scripts/check-public-release.sh` scans for private-context patterns. Extra
  project-specific needles can go in `scripts/private-context.local.txt`, which
  is gitignored, so the patterns stay local.
- Skills that talk to a service expect their own local config (for example a
  token file under `~/.config/`); they read it at run time and never carry it
  here.

## License

The repository's own files are MIT licensed (see [LICENSE](LICENSE)). Vendored
skills keep their upstream license and attribution — Apache-2.0 for the
Anthropic-authored skills, MIT for others; see
[docs/provenance.md](docs/provenance.md).
