# agent-kit (public)

This public repository contains reusable OpenCode skills, two OpenCode plugins,
and supporting scripts from VariableThe's agent-kit setup. It is a source
collection for inspecting, adapting, and testing those components; it is not a
complete machine-configuration installer.

## Public checkout scope

`install.sh` is included for reference, but it is not supported from this
public checkout. It expects files that are not present here, including
`opencode.base.json`, `overlays/`, `cli.json`, `src/beeper-mcp-shim.mjs`, and
`scripts/tailshare`. As a result, `./install.sh --check` exits before making
changes because its required base configuration is absent. Do not use
`--apply` from this repository.

The omitted configuration and integration files are not part of the public
release. This repository does not provide a complete OpenCode installation or
a replacement configuration.

## Included components

- `skills/` — self-contained skills and their helper scripts. Start with the
  relevant `skills/<name>/SKILL.md`.
- `plugins/tailshare/` — a plugin and helpers for rewriting local service URLs
  when a tailnet mapping is available.
- `plugins/hierarchy-guard/` — a permission-enforcement plugin. See its
  [README](plugins/hierarchy-guard/README.md) for behavior and limitations.
- `scripts/` — release checks, hierarchy-revert scripts, and token-budget
  tooling.
- `docs/` — design and integration notes for the included components.

`AGENTS.md` contains the contributor rules used by this project.

## Local validation

The Node checks cover the public plugin helpers. With Node 22.5 or newer:

```sh
npm ci
npm run check
npm test
```

Before publishing changes to this public repository, also run:

```sh
bash scripts/check-public-release.sh
```

Individual Python skills document their own runtime dependencies and commands
in their `SKILL.md` files. They are intentionally not installed by a single
repository-wide command.

## License

This repository is licensed under the [MIT License](LICENSE).
