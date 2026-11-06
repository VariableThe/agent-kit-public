# hierarchy-guard

OpenCode plugin (V2 API, written against opencode v2.0.29) that re-enforces
each agent's configured `permissions` rules after the runtime resolves its own
rules. Matcher parity was re-checked against a later 2.1.x release and the
semantics had not changed.

## What it enforces

Every agent's `permissions` list from the live `opencode.json` is
re-evaluated on each permission check. When the agent's own policy denies an
action, the plugin forces `effect: "deny"` — even if a client layer appended
a session-level `allow` that would otherwise win under the runtime's
last-match-wins resolution. Without this, read-only agents (e.g. `bishop`)
were observed executing shell/edit calls a client allow had re-enabled.

## Precedence

The plugin's `permission.hook("evaluate")` runs AFTER the runtime resolves
agent rules + session/client rules, and it can only tighten the decision: it
never upgrades `deny`/`ask` to `allow`, and it leaves agents with no
configured policy untouched. Pure helpers live in `policy.mjs` (no opencode
imports, unit-tested); `index.ts` is the thin hook wiring.

## Opt-out

`HIERARCHY_GUARD_OFF=1` disables the plugin entirely (setup returns early,
no hooks registered).

## Fail-safe

Every path is defensive: a missing/malformed config yields an empty policy
(never throws), unexpected event shapes are ignored, and setup never throws.
A broken plugin behaves exactly as if it were absent — it never breaks the
agent loop. For the shell tool, the `tool.hook("execute.before")` path is
audit-only (one stderr line on deny); enforcement there is the permission
hook, because the shell path ignores input mutation.

## execute tool

The execute (Code Mode JS) tool carries no `permission` field in v2.0.29 and
triggers no `permission.evaluate` events, yet in the general runtime its
sandbox exposes `require()`/`child_process` — a shell-equivalent bypass for
agents denied shell. The plugin gates it in `tool.hook("execute.before")`
through
`shouldNeuterExecute(deriveCapabilities(rules), resolveExecutor(process.env))`:
when that returns true, the hook replaces `event.input` with `{ code: "" }`
and logs the same structured stderr line. Unlike the shell path, the execute
path honors the replacement — the call fails before any JS runs, and a marker
written by `execSync` in the original input is never created. Agents whose
policy allows shell, agents whose policy allows delegation under a restricted
executor, and agents with no configured policy are untouched.

### Executor capability: host and restricted

The decision reads the agent's declared capabilities, never an agent name. On a
host executor — native OpenCode and any harness we do not recognise — Code Mode
reaches the filesystem, so an agent whose policy denies shell is always
neutered. On a restricted executor the execute tool can be a catalog bridge
rather than the general runtime the v2.0.29 gate was written for, so an agent
whose policy allows delegation keeps the catalog and every other agent is
neutered.

What follows is what was observed, not a standing guarantee. On the harness
build probed, Code Mode exposed no `require` or `process`, did not support
dynamic `import`, refused `fetch` on `file://` URLs, and left no marker file
when an `execSync`/`writeFileSync` escape was attempted, while the same
session's shell did create one. Those checks say the filesystem and process
escapes did not work there. They do not prove no sandbox of that shape can
reach them, so the plugin does not treat a restricted executor as harmless: it
keeps the catalog available only to agents the policy already lets delegate,
and the classification itself is a heuristic on `T3CODE_*` environment keys that
a changed harness can invalidate. Re-probe after a harness or runtime upgrade.

That catalog still carries real authority: on the build probed it reached the
browser tools, messaging, MCP servers and a remote sandbox, which is the other
reason `execute` under a restricted executor is not treated as read-only and is
not governed by this plugin. The neutering rule bounds which agents get that
surface, not what the surface can do.

The gate is the environment, not the client name: `isT3CodeHarness` returns
true when `process.env` has any own enumerable key beginning `T3CODE_` with a
non-empty string value. Prefix matching keeps it working when the harness
renames its variables. The failure mode is asymmetric: a session that carries
no such key — native OpenCode CLI or service — is classified `host` and gets the
strict path, while a non-harness process that happens to export a `T3CODE_*`
variable would be classified `restricted`. Detection was confirmed on the
sessions available at the time; it is a heuristic, not an identity check. Under
the restricted path, agents whose policy denies delegation keep the neutering,
and `permission.hook("evaluate")` remains the authoritative enforcer
regardless.

## Agent identity under a host harness

A host harness runs its primary thread as `build` and does apply that agent's
configured system text. Passing the intended worker to the subagent tool's
`agent` parameter **does** set the child session's identity, and enforcement
matches that identity: a planning agent runs with shell/edit denied by this
plugin, an implementation agent with them allowed. So a delegated session is
policed as the agent it was asked to be; only the primary thread is fixed at
`build`.

## Subagent model routing

The native OpenCode `subagent` tool (tool name `subagent`) resolves its child
model as `input.model ?? agent.model ?? parentSession.model` (opencode v2.0.29
source). An omitted `model` therefore inherits the parent's model. The plugin
listens on `tool.hook("execute.before")` for that tool and routes the child by
the agent's declared class in `MODEL_ROUTING` (`policy.mjs`), reading the
configured model from the live opencode config (`agents.<name>.model`):

- `pinned` (bishop, knight): always the configured model. A different input is
  corrected.
- `explicit` (mercenary): any non-empty input model is honored; an omitted one
  defaults to the configured model.
- `escalation` (witch, mage, grand-bishop, knight-commander): only the declared
  paid models are honored; anything else is corrected to the configured model.

The old "paid escalation agents are never model-checked" exemption is gone. An
escalation agent is never blocked on model grounds, but a silent downgrade and
an unplanned paid model are both corrected. Routing answers which model, never
whether the call may run.

The mutation reaches the tool because the subagent tool's own internal
`execute.before` hook uses the same in-place `N.input = P` mechanism. This is a
corrective pin, not a deny: the call proceeds on the routed model, and every pin
is logged as a structured `subagent-model-pin` stderr line.

The hook is the only mechanism available. A tool hook's return value is ignored
and a thrown error does not reliably block (the hook is wrapped in
`Effect.try`), so the channel is in-place input mutation. The guarantee is
therefore "a gated child always runs its routed model", not "a bad call is
refused".

### T3 app-owned tools are not hook-visible

`delegate_task` and `functions.subagent` are T3 app-owned tools: they do not
appear in the opencode binary and do not fire `tool.hook("execute.before")`.
agent-kit therefore cannot hard-enforce their child model — only the caller-side
rules in `AGENTS.md` and the build/monarch system text apply:
- native OpenCode `subagent`: explicit `agent` + configured `model`,
  mechanically pinned by the plugin;
- T3 `functions.subagent`: explicit `agent` + exact configured `model`,
  caller/prompt-enforced only;
- T3 `delegate_task`: explicit `target.providerInstanceId` + `target.model`,
  caller/prompt-enforced only.
If T3's dispatcher bypasses the OpenCode tool hook (as the absent binary symbol
indicates), only T3's own dispatcher can enforce the worker model.
`validateDelegateTaskModel` in `policy.mjs` is a shape validator for
callers/tests, not an enforcement point (it requires explicit target fields and
does not map a role/agent to a configured model).

## Delegation depth

Root-ness is derived from the policy, not a literal name list: any agent whose
declared rules allow `subagent` on a target is a root orchestrator (in the
shipped roster, `build` and `monarch`). A worker session that reaches `subagent` has its input
neutered — `input.agent` becomes an unmatchable sentinel and `input.prompt`
carries the reason — and a structured `delegation-blocked` line is logged.
`resolveSubagentInput` additionally neuters a dispatch to an agent the loaded
policy does not declare, and to another orchestrator, because depth would
exceed 1. An identity the policy cannot classify (no policy, empty policy, or an
undeclared agent) is blocked from delegating unless it is one of the floor
orchestrators, `build` or `monarch`: delegation fails closed. The
rewrite is a shallow copy of the input (`{ ...input }`), so the mutation reaches
the fields the tool itself reads.

## Install wiring

The installer symlinks the whole directory to
`~/.config/opencode/plugins/hierarchy-guard`, which OpenCode
auto-discovers — no config change. `package.json` carries no dependencies:
`@opencode/plugin` is host-provided at runtime, never vendored. Unit tests
live at `test/hierarchy-guard-policy.test.mjs` and run with `npm test`.
`test/hierarchy-guard-e2e.sh` is the opt-in end-to-end check (`npm run
test:e2e`): it needs the `opencode` binary on `PATH` and network access to a
free model, so it is not part of the default test run.

The full model — capability derivation, session kinds, invariants, the
enforcement matrix per transport, and the known gaps — is in
[`docs/hierarchy-model.md`](../../docs/hierarchy-model.md).
