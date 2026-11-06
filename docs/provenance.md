# Provenance and exclusions

Where the contents of this repository came from, what changed on import, and
what is deliberately not published here.

## This repository

Everything not listed below was written for this repository and is MIT licensed
under the top-level [LICENSE](../LICENSE).

## Vendored skills

Vendored skills are copied verbatim except where the "changes on import" column
says otherwise. Each keeps its upstream license file and its frontmatter
`source:` line, so attribution travels with the copy.

| Skill | Upstream | License | Changes on import |
| --- | --- | --- | --- |
| `academy-guide` | `anthropics/skills` → `skills/academy-guide` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `algorithmic-art` | `anthropics/skills` → `skills/algorithmic-art` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `brand-guidelines` | `anthropics/skills` → `skills/brand-guidelines` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `claude-api` | `anthropics/skills` → `skills/claude-api` | Apache-2.0 (`LICENSE.txt`) | frontmatter `source:` line; two documentation placeholders hardened (below) |
| `discernment-nudge` | `anthropics/skills` → `skills/discernment-nudge` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `frontend-design` | `anthropics/skills` → `skills/frontend-design` | Apache-2.0 (`LICENSE.txt`, upstream copy omits the appendix) | none beyond the frontmatter `source:` line |
| `internal-comms` | `anthropics/skills` → `skills/internal-comms` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `mcp-builder` | `anthropics/skills` → `skills/mcp-builder` | Apache-2.0 (`LICENSE.txt`) | frontmatter `source:` line; `reference/node_mcp_server.md` tracks a newer upstream version than the published original; three documentation placeholders hardened (below). `reference/python_mcp_server.md` is otherwise unchanged. |
| `skill-creator` | `anthropics/skills` → `skills/skill-creator` | Apache-2.0 (`LICENSE.txt`) | frontmatter `source:` line. `scripts/__init__.py` is kept deliberately: `python -m scripts.*` entry points need it even though it is empty. |
| `slack-gif-creator` | `anthropics/skills` → `skills/slack-gif-creator` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `theme-factory` | `anthropics/skills` → `skills/theme-factory` | Apache-2.0 (`LICENSE.txt`) | frontmatter `source:` line; the compiled `theme-showcase.pdf` sample is not republished, and `SKILL.md` now describes the themes under `themes/` instead of pointing at it |
| `web-artifacts-builder` | `anthropics/skills` → `skills/web-artifacts-builder` | Apache-2.0 (`LICENSE.txt`) | frontmatter `source:` line; `scripts/SHADCN-UI-LICENSE.txt` and `scripts/SHADCN-UI-NOTICE.md` added for the bundled components (below), and `scripts/init-artifact.sh` now copies them into generated projects. The tarball itself is unchanged. |
| `webapp-testing` | `anthropics/skills` → `skills/webapp-testing` | Apache-2.0 (`LICENSE.txt`) | none beyond the frontmatter `source:` line |
| `unslop` | `poteto/noodle` → `.agents/skills/unslop` | MIT (declared in frontmatter) | none; no separate license file is shipped upstream either |

`caldav` declares `license: MIT` and is written for this repository; it is not a
vendored copy. Its example calendar names are placeholders (`MyCalendar`), not
anyone's real calendar.

### Bundled shadcn/ui components

`skills/web-artifacts-builder/scripts/shadcn-components.tar.gz` is a
redistribution, so it needs the component license alongside it. It carries none
of its own, so two files were added:

- `SHADCN-UI-LICENSE.txt` — shadcn/ui's MIT license, `Copyright (c) 2023
  shadcn`, copied verbatim from shadcn/ui (sha256
  `1564074e13439397221ffd522e2e504d56561994a23d371aa5e3ad43e4f5423f`).
- `SHADCN-UI-NOTICE.md` — the public commit where this archive is verified
  (`anthropics/skills` `ef740771ac90`, sha256
  `1296f069e825de7933de7e8ac63672495cacce3f88f2dc45554dbcddbcbca60f`), how the
  files differ from the closest matching shadcn/ui revision, and the
  verification commands.

`init-artifact.sh` treats both files as required input: it exits before
creating anything if either is missing, and after extraction copies them into
`src/components/ui/`, so the notice travels with anything built from these
components. `test/web-artifacts-notice.test.sh` (`npm run test:notice`) checks
the hashes, the copy, and the missing-notice failure with a stub `pnpm`. The
archive is not repacked; adding files to it would break the byte-identity above.

### Placeholder hardening

Five upstream documentation snippets wrote an `Authorization` header whose
placeholders were the bare words `token`, `token123` or `...`. Credential
scanners flag them, and a scanner should not have to weaken its pattern for
sample text — real short tokens exist. The snippets now name the value
`$MCP_TOKEN` (shell examples) or `<TOKEN>` (prose): unambiguously not a
credential, and still a fill-in-the-blank:

| File | Change |
| --- | --- |
| `skills/mcp-builder/scripts/evaluation.py` | one usage example in the module docstring |
| `skills/mcp-builder/reference/evaluation.md` | two `curl`/CLI examples |
| `skills/claude-api/shared/managed-agents-onboarding.md` | one sentence about `ant auth print-credentials` |
| `skills/claude-api/shared/preserved-thinking-migration/drop_block_probe.py` | one docstring example |

These are the only edits to those files; no instruction, code path or claim
changed. The scanner keeps its full pattern, including short `Bearer` values.

## Not published here

Deliberately absent, with the reason:

| Excluded | Reason |
| --- | --- |
| Anthropic document skills (`docx`, `pptx`, `xlsx`, `pdf`) | Distributed upstream under Anthropic's service terms, not a redistribution license. Not vendored. |
| A design/canvas skill | Bundles fonts whose licenses are not included. Held until that is resolved. |
| A document co-authoring skill | No license at all upstream. Held. |
| A contact-resolution skill | Not finished or reviewed at the time of this snapshot. Held. |
| A diagramming skill pair, and its vendored tests | MIT-licensed upstream, but the published copies are saturated with one machine's setup (absolute home paths, local service names, ports, a personal domain). Held rather than published in host-specific form. |
| A report skill, and a round-table skill | Depend on a private architecture and a private provider. Not portable as written. |
| Provider and agent configuration, model rosters, overlays | Environment-specific. The installer treats `opencode.json` as yours; this repo does not ship one. |
| Machine inventories, host names, endpoints, credentials, personal data | Private by definition. |
| Compiled theme sample (`*.pdf`) | A build artifact, gitignored; the source themes it demonstrates are included. |

Two working sets that existed upstream at snapshot time — an unpushed merge and
an in-review skill — are also excluded. Neither was copied, and no upstream
branch or history is imported here: this repository's history is its own.

## Reproducing a comparison

To verify a vendored skill against upstream, fetch the upstream path named in
its `source:` line and diff, allowing for that one frontmatter line:

```bash
# with the upstream checkout at $UPSTREAM
diff -r "$UPSTREAM/skills/algorithmic-art" skills/algorithmic-art
```

Files that should come back identical: everything except each `SKILL.md`, the
`mcp-builder` reference file noted above, the files added or edited for the
bundled shadcn/ui components, and the five placeholder lines listed under
"Placeholder hardening".
