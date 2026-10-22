# The hierarchy model

How session authority is derived from `agents.<name>.permissions`, and where the
plugin can and cannot enforce it. Code references name the function that
decides the outcome.

## 1. The model in one paragraph

Rules describe what a session may do, not which harness it runs in. A session's
authority is the intersection of (a) what its agent name declares in
`agents.<name>.permissions`, (b) where it sits in the delegation tree, and (c)
what the transport can observe. Harness is an input only twice: it determines
whether the Code Mode executor can reach the host, and it determines whether the
session's tools fire OpenCode hooks at all.

## 2. Capabilities are derived, not declared

`deriveCapabilities(rules)` in `policy.mjs` computes four capabilities from the
agent's own rule list. Nothing else is consulted, and no new config keys exist:
`permissions` stays the single source of truth.

| capability | derivation |
| --- | --- |
| `write` | `resolveEffect("edit", "*", rules).effect !== "deny"` |
| `execute` | `resolveEffect("shell", "*", rules).effect !== "deny"` |
| `targets` | every rule that is `allow` on `subagent` whose resource itself resolves to `allow` |
| `delegate` | `targets.length > 0` |

`execute` is deliberately coarse. A read-only allowlist counts as *not*
permitted, because the blanket `shell/*` deny still matches resource `*` — Bishop's
`ls *`, `cat *` and `git status *` allows do not make it execute-capable. A lone
`subagent * deny` leaves `delegate` false because no target resolves to `allow`.

This derivation feeds only cross-cutting decisions: the delegation right and
execute neutering. Every individual call is still resolved resource-exactly by
`permission.hook("evaluate")`. A future partial rule such as `edit src/** deny`
stays exactly enforced per path; the coarse bit never overrides it.

## 3. Session kinds

| kind | identifying rule from the config | members | write | execute | delegate |
| --- | --- | --- | --- | --- | --- |
| root orchestrator | `delegate === true` | build, monarch | no | no | yes |
| worker-writer | write + execute, no delegate | knight, mercenary, mage, knight-commander | yes | yes | no |
| auditor | no write, shell blanket-denied, read-only allowlist, no delegate | bishop, witch, grand-bishop | no | no | no |
| planner | no write, no delegate, shell blanket-denied | plan | no | no | no |
| unknown | agent has no `permissions` entry | anything else | — | — | — |

The planner row matches the auditor row: every capability is denied, so Code
Mode is neutered there too. The unknown row means no derived
authority and no plugin enforcement: `rulesFor` returns `undefined` and every
hook returns untouched.

## 4. Invariants

**I1 write.** `permission.hook("evaluate")` resolves `edit` against the agent's
rules and forces `deny` when the result is `deny` (`index.ts:251`). Enforced per
resource, not per agent.

**I2 shell.** Same hook, action `shell`. `tool.hook("execute.before")` also
evaluates the command, but only logs: running before the permission check, it
would let a mutated command mask the deny message, so the mutation is skipped and
the permission hook stays authoritative (`index.ts:364-381`).

**I3 execute aliasing.** Code Mode has no permission gate. On a host executor it
reaches the filesystem, so `execute.before` treats it as shell and neuters the
input to `{ code: "" }` when `shouldNeuterExecute` says so. On a restricted
executor it is a **catalog bridge**, and the plugin applies the only enumerable
proxy available: an agent whose policy denies delegation is neutered, an agent
that may delegate keeps the catalog. The residual authority is real — under T3
Code the catalog includes the browser tools, messaging/Beeper, the MCP servers
and the remote sandbox. `execute` there is **not** a read-only capability and the
plugin does not govern it. The neutering rule is a bound on which agents get that
surface, not a proof about what the surface can do.

**I4 delegation right.** `resolveNestedDelegation` returns a block unless the
agent is allowed to delegate, and rewrites `input.agent` to the unmatchable
sentinel `BLOCKED_DELEGATION_AGENT`. Two modes: **policy-derived** when the
loaded policy declares the identity — its derived `delegate` decides — and
**floor** when it does not (no policy, empty policy, undeclared agent), where
only `build` and `monarch` may delegate. The floor **fails closed**: an identity
the policy cannot classify is blocked. Enforced for the native `subagent` tool
only.

**I5 dispatch target.** `resolveSubagentInput` blocks a dispatch to an agent the
loaded policy does not declare, and one whose derived `delegate` is true (depth
would exceed 1). Enforced for the native `subagent` tool only; app-owned
dispatchers have no `agent` parameter to inspect.

**I6 depth 1.** The combination of I4 and I5, plus the required leaf-worker
clause in every dispatch prompt. Mechanism covers the native `subagent` tool;
everywhere else depth is policy, not enforcement.

**I7 no `ask` in any policy.** The runtime would prompt, and the plugin only
forces `deny`, so an `ask` rule would leave a live prompt in the path.
Not enforced by the plugin: `test/hierarchy-guard-config.test.mjs` lints the
shipped roster and fails if any rule uses `ask`. `ruleEffect` treats an unknown
or missing effect as `ask`.

## 5. Enforcement matrix

| session kind | capability in question | delegation right | enforcement mechanism | when unenforced, what is actually true |
| --- | --- | --- | --- | --- |
| root orchestrator (build, monarch) | write | yes | `permission.hook("evaluate")` | n/a — deny is forced |
| root orchestrator | shell | yes | `permission.hook("evaluate")` | n/a — deny is forced; `execute.before` on the shell path logs only |
| root orchestrator | execute, host executor | yes | action-level input neutering (`execute.before` → `{ code: "" }`) | n/a |
| root orchestrator | execute, restricted executor | yes | none; `shouldNeuterExecute` returns false by design | the Code Mode catalog is live and ungoverned (browser, messaging, MCP, remote sandbox) |
| worker-writer (knight, mercenary, mage, knight-commander) | write, shell | no | `permission.hook("evaluate")` | n/a |
| worker-writer | `subagent` | no | `resolveNestedDelegation` neuters `input.agent` | n/a — the child id resolves to no agent |
| auditor (bishop, witch, grand-bishop) | write, shell | no | `permission.hook("evaluate")` | n/a — only the explicit read-only allowlist survives |
| auditor | execute, restricted executor | no | `shouldNeuterExecute` → input neutering | n/a |
| auditor | execute, host executor | no | `shouldNeuterExecute` → input neutering | n/a |
| planner (`plan`) | write | no | `permission.hook("evaluate")` | n/a |
| planner (`plan`) | shell, execute | no | `permission.hook("evaluate")` for shell; `shouldNeuterExecute` → input neutering | n/a — the `shell/*` deny makes the derived `execute` bit false |
| unknown agent | all | unknown | none — `rulesFor` returns `undefined` | no derived authority, no plugin enforcement |
| T3 thread child via `delegate_task` | write, shell | inherited (`build`) | none in the plugin | identity is the caller's `build`: it can write nothing and run no shell command, yet it holds the app-owned delegation tools, so it can recurse. The caller pre-check and the worker handshake are policy, not mechanism. |

## 6. Enforcement by transport

### Hook-visible: native OpenCode

`edit`, `shell`, `subagent` and `execute` are the only tools in the binary, so
only they can be governed here. Two channels exist and neither can refuse a call:

- `permission.hook("evaluate")` runs after agent and session rules resolve and
  can only force `deny`. It never upgrades `deny` or `ask` to `allow`.
- `tool.hook("execute.before")` can only mutate the input object in place. Return
  values are ignored and a throw does not block.

Therefore blocking means neutering an input, never refusing a call: the subagent
target id becomes an unmatchable sentinel, the execute payload becomes an empty
code string.

### App-owned: T3 Code

`delegate_task`, `functions.subagent`, `t3_thread_launch` and `create_threads`
contain no `agent` parameter and fire no hook. The honest ladder, weakest to
strongest:

- **L0 structural.** App tools are injected into T3 thread sessions only, and
  every T3 thread runs `build`, so no worker-capability session can hold them.
  This bounds *who* can recurse, not whether.
- **L1 policy.** `delegate_task` is not a work tool. It is for read-only or
  catalog-only sub-tasks.
- **L2 bounded failure.** The dispatch handshake: state the required capability,
  abort with one line if denied.
- **L3 upstream.** The only real fix is an `agent` parameter or a `maxDepth`
  check in T3's dispatcher.

Measured consequence of L0–L2 as they stand: three generations produced no
output, and the parent ended `interrupted` with a summary that only described its
dispatch.

## 7. Model routing

`MODEL_ROUTING` in `policy.mjs` assigns each routed worker a class, and
`resolveModelRouting` is the single authority for what a dispatch runs.

- **pinned** (bishop, knight): always the configured model. A different input is
  corrected, never honored. The config remains the source of truth.
- **explicit** (mercenary): any non-empty input model is honored; an omitted one
  falls back to the configured model and is corrected.
- **escalation** (witch, mage, grand-bishop, knight-commander): only the declared
  `allowed` paid models are honored. Anything else falls back to the configured
  model, or `allowed[0]` when the config declares none.

An agent with no class but a configured model is still routeable: an explicit
input is honored, an omitted one defaults to the configured model. An agent with
neither is `known: false` and its input is left alone.

The config is read live (`loadAgentModels`, invalidated on mtime and size) and
remains the source of truth. A pinned agent with no configured model has no
fallback pin: the dispatch input is left to the caller.

Escalation agents are no longer exempt from model checking. They may run only
their declared paid models, with Witch's declared fallback among them, so a
silent downgrade and a silent upgrade are both corrected and logged. Routing
answers "which model", never "may this run": a paid agent is never blocked on
model grounds.

## 8. The scheduled daily run

Exactly one scheduled task exists: id
`scheduled-task:83ceb38b-2cb4-42c1-b166-8d475f8a8f98`, title "Daily model
check", project `decon` (`/home/decon`), `fixed_time` 09:30 local (IST,
UTC+05:30, so 04:00Z), no weekday filter, `enabled=1`,
`runtime_mode=full-access`, `interaction_mode=default`, model
`opencode/deepseek-v4.1-flash-free`. `run_count=4`; the last run, 2026-10-20,
succeeded.

Revision 3 of the prompt lives in `docs/scheduled-daily-model-check/after.txt`
(7,790 bytes). The stored task row
was updated 2026-10-21 and now holds revision 3 (stored prompt is
byte-identical to `after.txt` modulo the trailing newline).

The task row stores `model_selection_json`, `runtime_mode`,
`interaction_mode` and `workspace_strategy_json`. There is no agent field: the
T3 scheduler starts an ordinary T3 thread, and a T3 thread runs `build`. The
scheduled run's identity is therefore `build`.

Capabilities the run needs, derived from its own prompt, not assumed:

- `read` on the repository.
- Code Mode under T3, for the allowance docs, the OpenRouter catalogs, the
  OpenCode usage tools and the Beeper send.
- Delegation for everything that needs shell or write: `git`,
  `./install.sh --check`/`--apply`, `npm`, `gh`, Tailscale SSH to your-host,
  and writing `~/.local/state/agent-kit/model-watch.json`. No write or shell
  for the parent itself.

Observed on both recorded runs (2026-10-19, 2026-10-20): the parent's own
`shell` calls (`git status`, `ls`) and its `edit` of `model-watch.json` were
denied by the plugin with the standard message, and it completed the work
through 5 native-subagent children on 2026-10-20 and 4 on 2026-10-19. Both runs
reported "no change" with a correct report. `runtime_mode=full-access` therefore
does not widen `build` past its declared policy; this plugin is the reason.

Under this model the run keeps working unchanged: `build` derives `delegate`,
so it keeps the Code Mode catalog under T3, its only route to MCP, fetch and
Beeper; write and shell stay denied; its children are worker-writers with their
own capabilities; depth 1 is satisfied because `build` is the root and every
child is a worker.

Non-interactive safety:

- No agent in the shipped roster uses `ask` (linted by
  `test/hierarchy-guard-config.test.mjs`), so the run cannot raise a prompt.
- A denial is a one-line message that reaches the tool result, so a missing
  capability fails fast instead of blocking.
- The dispatch rule refuses a target that lacks the required capability, so the
  run cannot park work on an agent that will sit idle.

Behaviour change: **none**.

| | before | after |
| --- | --- | --- |
| parent identity | `build` | `build` |
| parent write and shell | denied | denied |
| parent Code Mode | `build` keeps catalog | unchanged |
| parent delegation | allowed to the seven named workers | unchanged |
| child write and shell | allowed | unchanged |
| depth | 1 | 1 |

The only widening on this branch, `monarch` keeping the catalog, does not reach
this run because T3 threads run `build`.

Pre-existing defect found while writing this, not caused by this work and not
fixed: the task prompt instructs the parent to run `git`, `./install.sh`, `gh`
and to write `model-watch.json` itself, all of which are denied, so it works
around them by delegating. Recommended: rewrite those steps to name the worker
explicitly and to carry the leaf-worker clause. The schedule lives in T3's
database, not in this repository, so the fix is an `update_scheduled_task`
call, deliberately not made here.

## 9. Known gaps and unenforced region

- **T3 app-owned tools.** No hook, no permission action, no `agent` parameter.
  Nothing in the plugin observes them, so no plugin change can close this.
- **Code Mode catalog authority under T3.** Browser, messaging, MCP and the
  remote sandbox are reachable through `execute`. The plugin bounds which agents
  get that surface; it does not inspect what the surface does.
- **Depth is identity-proxied.** The hook sees `sessionID`, not a parent id, so
  nesting is inferred from the agent's own capabilities. A session whose
  identity is not the one that delegated cannot be classified by tree position.
- **No captured trace on the native path.** `HIERARCHY_GUARD_TRACE=1` exists, but
  a plain `opencode run` does not export it into the process that runs the hooks,
  so no trace line was captured. Unverified.
- **`delegate_task.target.options`.** Whether it can ever select an agent is
  unverified; the namespace became unavailable mid-probe. If it can, that is the
  missing identity switch.

## 10. Decision log

| decision | alternative rejected | why |
| --- | --- | --- |
| Capability-derived rules. | Separate rule sets per harness. | Two policies drift. The incident happened because the rule named a harness and an agent instead of a capability. |
| Root-ness derived from the policy. | A literal orchestrator list. | The literal `["build"]` blocked `monarch`, a primary orchestrator with identical declared rights — a real regression found while writing this model. |
| `execute` gated by executor capability, not by an agent name. | Neuter every agent the way native sessions are neutered. | It removes Monarch's entire catalog under T3, where the sandbox cannot reach the host, and breaks the scheduled daily task, which has no other route to MCP and the app tools. |
| `execute` under a restricted executor is still a bridge with real authority. | Treat the restricted executor as read-only. | Browser, messaging, MCP and the remote sandbox are reachable through it. |
| Fail open on unreadable or unknown config. | Fail closed on every capability. | A broken config, unknown event shape or new harness must degrade to no extra enforcement; inventing denies would change sessions the config never described. Consequence accepted for every capability except delegation: an unreadable config removes enforcement, but delegation falls closed on the floor (next row). |
| Delegation fails closed on an unavailable policy. | Degrade to no extra enforcement like every other capability. | Recursion is the failure that produced three generations and a 90-minute hang, while a false deny costs one line and leaves the loop running. The general fail-safe rule is about not inventing denies for capabilities the config did describe; an undeclared identity describes nothing. |
| Model routing corrected from config with declared escalation allowlists. | Keep the "paid escalation agents are never model-checked" exemption. | It permits both a silent downgrade and an unplanned paid model. |
| Per-call resource-exact enforcement. | Derive one coarse "can write" bit and skip the hook. | Coarse bits cannot express path or command scoping. |
| Depth enforced only where hook-visible. | Claim app-tool enforcement. | Nothing in the plugin observes those tools; a policy claim of enforcement would be false. |
| Close the `plan` shell hole in config, not in the plugin. | Special-case `ask` on `shell/*` inside `shouldNeuterExecute`. | `deriveCapabilities` correctly mirrors declared policy; an agent-specific exception in the plugin would be the same name-based rule this model removed, and the config is where the declaration was missing. |

### Widening

Behaviour that got wider, and the rule that justifies it:

- `monarch` now keeps the Code Mode catalog under T3 Code, the same rule that
  already applied to `build`. Both declare delegation.
- Escalation agents are now corrected rather than exempt, which changes which
  model actually runs.
- `validateSubagentModel` now reports a correction for escalation agents instead
  of always returning valid.

### Narrowing

Behaviour that got narrower, and the rule that justifies it:

- An identity the loaded policy cannot classify is now blocked from delegating
  by the floor (`build`, `monarch`). The nesting guard fails closed; every other
  capability still fails open. An absent policy declares nothing to enforce, so
  the only safe assumption about an unidentified identity is that it is not an
  orchestrator.

## 11. Compatibility

- **Existing sessions.** No config migration. Every rule above is derived from
  the current `opencode.json`. The only identities whose behaviour changes are
  the ones named under Widening and Narrowing.
- **`plan` shell rule.** Closed on `fix/plan-readonly-shell`: `agents.plan` now
  declares `shell * deny`, so its derived `execute` is false and a plan session
  is neutered like an auditor. The gap mattered because Code Mode is not gated by
  `edit`: a read-only-by-declaration planner still kept the sandbox that can
  write files.
- **Branch `feat/delegation-depth-guard`** (204c2dd, unmerged). This branch
  extends and supersedes its mechanism rather than rebasing it. It keeps its
  nesting block and its tests; it replaces the literal root list with the
  policy-derived rule (fixing `monarch`), and adds target validation and routing
  classes. The depth-guard commit belongs in the same PR as this branch rather
  than merging separately. The depth-guard branch should not be
  merged on its own once this exists, because on its own it blocks `monarch`.
- **Live install.** `~/.config/opencode/plugins/hierarchy-guard` is a symlink to
  this repository's plugin directory, so the running guard is whatever this
  branch contains. No `install.sh --apply` is needed for plugin code, and
  `opencode.json` is unchanged by this work.

## 12. Probe transcript

- T3 Code, 2026-10-20 scheduled run, identity `build`:
  `hierarchy-guard: agent "build" is not permitted to use "shell" for "git status --short --branch" (rule "shell"/"*")`
  and
  `... "edit" for ".local/state/agent-kit/model-watch.json" (rule "edit"/"*")`.
  Observed. `permission.evaluate` fired and its message reached the tool result.
- T3 Code, this session, 2026-10-21, identity `build`: `ls -la` and `git status`
  were refused with the same message shape. Observed. `execute` calls in the same
  session succeeded, consistent with the restricted-executor rule and with
  `build` being a delegator. Observed.
- T3 Code: `delegate_task` and the `t3_thread_*` tools produced no
  `hierarchy-guard` line and later became unavailable mid-session ("Unknown
  tool"). Observed that they are not hook-visible; unverified whether an option
  can switch the child's agent.
- Native OpenCode v2.1.1: `opencode run --agent bishop ... shell` failed with
  `Error: Permission denied: shell` and no plugin line; `--agent knight` executed
  shell successfully with no plugin line. The attribution is therefore
  unverified: the client process does not export `HIERARCHY_GUARD_TRACE` to the
  process that runs the hooks. Next command to capture it: run the service under
  a throwaway `HOME` with `HIERARCHY_GUARD_TRACE=1` in its environment
  (`test/hierarchy-guard-e2e.sh` already does this), then replay one shell call
  per agent.
- Matcher parity: the installed v2.1.1 binary contains `Eb(e,r)` with the same
  chain as `compilePattern` — backslash normalisation, metachar escaping, `*` to
  `.*`, `?` to `.`, the trailing `" .*"` to `"(.*)?"` quirk, and `^...$` with the
  `s` flag. Observed. The exported name changed from `Q_`; semantics did not.

## 13. What to do next

- Capture the native trace with the throwaway-`HOME` method, so the plugin's
  decisions on the native path are observed rather than inferred.
- Probe `delegate_task.target.options` for an agent switch. If there is none,
  file the upstream request for an `agent` parameter or a `maxDepth` check.
