# Global Agent Rules — VariableThe

Applies to every project/session. Project-local AGENTS.md overrides; user instructions override all.

## Communication
- Short, direct, factual. No superlatives, praise, or emojis unless asked.
- Reference code as `file_path:line_number`. Never invent URLs.
- Never report localhost/127.0.0.1/0.0.0.0/::1 or private-network addresses as a result. Expose a service through your environment's approved sharing mechanism and report that link.
- State hypotheses/evidence separately.
- Never reuse Beeper IDs across transports; resolve recipient by name and confirm title.

## Verification
- Verify via execution: typecheck, lint, build, tests.
- Prefer real data over fixtures. Inspect files before claims.

## Code Conventions
- Minimal diffs; edit existing files; no new deps without need; reuse shared code.
- Respect repo design/theme/UX. Keep trees clean.

## Tool Use
- Prefer dedicated file tools over shell. Batch independent calls in parallel; sequence dependent ones. Never poll background work.
- Never write a tool call in prose; emit the actual invocation. If you catch yourself describing a dispatch, stop and emit the real tool call.

## Thread Todo
Real work runs on a disk-backed list: `python3 skills/thread-todo/scripts/thread_todo.py`.
- Scope `--thread <t3 threadId>` (from `t3_thread_configuration`) per thread; cwd scope by default.
- Start: `resume`; then `init` and add the plan. Per item `start ID` → `done ID`; stuck: `block ID --note "why"`.
- Notes hold the context a replacement needs. Subagents share the parent's `--scope <name>`.
- End: `close --summary "outcome"`. Never leave unfinished work only in chat.

## Quality Bar
- Be specific/contextual, never generic. No filler in user-facing copy. No hollow UI backed by no data/endpoint. Taste: generate options, reject structurally, keep survivors.

## Subagent Model Enforcement
Routing classes in `plugins/hierarchy-guard/policy.mjs`: pinned agents always run their configured model; explicit agents honor a caller-supplied model; escalation agents honor only their declared models. Set the classes and models for your own roster in that file.
- Native subagent: pass explicit `agent`+`model`; the plugin pins pinned agents to their configured model; abort+report if unverifiable.
- T3 `functions.subagent`: explicit `agent`+exact configured `model`.
- T3 `delegate_task`: set `target.providerInstanceId`+`target.model`; verify runtime child; abort+report if unverifiable.
- Plugin pins native subagent only; T3 app-owned tools not hook-visible.
- Escalation agents run only the models declared for them in `policy.mjs`. Never silently replaced.
- An orchestrator's own session model is not subject to routing rules.

## Delegation Depth (depth 1)
- Only build/monarch may delegate. Workers never call delegation tools; stop+report if help needed.
- Check target before dispatch; no delegating targets; no write/execute to incapable agents.
- Every dispatch prompt opens with: "You are a leaf worker. Do not call subagent or delegate_task; do the work yourself."
- Use native subagent for repo work and verify child identity. T3 delegate_task inherits caller identity.
- Never chain delegations. Recursion fingerprint: interrupted/cancelled status describing a dispatch.
- One task = one fresh subagent. Never pass a prior sessionID (native subagent) or childThreadId/taskId (T3) to revive a completed child; a completed subagent self-terminates — treat it as dead and spawn a fresh one with a self-contained prompt carrying all needed context.
- Full model: `docs/hierarchy-model.md`.

## Git Workflow
- Ask before pushing. PRs only, never direct to main. Branches `feat/...`, `fix/...`, `docs/...`.
- Pre-PR checks (lint/typecheck/tests/build). Focused PRs. Conventional Commits.
- Stale-PR check before every push; if merged/closed/absent, new branch+PR.
- New project with no git repo: ask before git init; never init unprompted.
- Report PR link.

## Escalation Ladder
- A) Normal: the planning agent plans, the implementation agent implements, both on their configured models.
- B) On CONFIRMED capacity failure (HTTP 429, rate-limit message, quota error): retry worker once after retry window, then hand unfinished task to Mercenary with a verified free model, passing plan, repo state, completed work, verbatim errors, remaining criteria; no parallel workers on same files.
- C) Escalation only when the free path stalled (the fallback agent tried >=2 distinct free models, or >=3 consecutive capacity failures across the normal roster); state the reason if earlier; route planning and implementation to the declared escalation agents.
- D) The deep-reasoning escalation agent only for genuinely hard reasoning/refactoring/debugging after free attempts, or when the escalation path cannot solve it.
- Error classification: auth, permission, missing-model, config, network/server without rate-limit evidence, and real test failures are NOT capacity failures; diagnose first. Never fall back to a paid model silently. Record paid invocations with agent id, model id, reason, work, tests.
