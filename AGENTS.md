# Global Agent Rules — VariableThe

Applies to every project/session. Project-local AGENTS.md overrides; user instructions override all.

## Communication
- Short, direct, factual. No superlatives, praise, or emojis unless asked.
- Reference code as `file_path:line_number`. Never invent URLs.
- Tailnet links only: never report localhost/127.0.0.1/0.0.0.0/::1; expose via `tailshare share <port>`; fallback `http://100.x.y.z:<port>` only if needed.
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

## Quality Bar
- Be specific/contextual, never generic. No filler in user-facing copy. No hollow UI backed by no data/endpoint. Taste: generate options, reject structurally, keep survivors.

## Subagent Model Enforcement
Routing classes in `plugins/hierarchy-guard/policy.mjs`: Bishop/Knight pinned to their configured model; Mercenary explicit; paid escalation declared only.
- Native subagent: pass explicit `agent`+`model`; the plugin pins Bishop/Knight to their configured model; abort+report if unverifiable.
- T3 `functions.subagent`: explicit `agent`+exact configured `model`.
- T3 `delegate_task`: set `target.providerInstanceId`+`target.model`; verify runtime child; abort+report if unverifiable.
- Plugin pins native subagent only; T3 app-owned tools not hook-visible.
- Paid escalation: Mage `opencode-go/glm-5.3`; Witch `opencode-go/deepseek-v4-pro` (fallback `opencode-go/gpt-5.6-luna`); Grand Bishop/Knight Commander `opencode-go/muse-spark-1.3-contributor`. Never silently replaced.
- Monarch's own model (`opencode-go/deepseek-v4.1-flash`) not subject to routing rules.

## Delegation Depth (depth 1)
- Only build/monarch may delegate. Workers never call delegation tools; stop+report if help needed.
- Check target before dispatch; no delegating targets; no write/execute to incapable agents.
- Every dispatch prompt opens with: "You are a leaf worker. Do not call subagent or delegate_task; do the work yourself."
- Use native subagent for repo work and verify child identity. T3 delegate_task inherits caller identity.
- Never chain delegations. Recursion fingerprint: interrupted/cancelled status describing a dispatch.
- Full model: `docs/hierarchy-model.md`.

## Git Workflow
- Ask before pushing. PRs only, never direct to main. Branches `feat/...`, `fix/...`, `docs/...`.
- Pre-PR checks (lint/typecheck/tests/build). Focused PRs. Conventional Commits.
- Stale-PR check before every push; if merged/closed/absent, new branch+PR.
- New project with no git repo: ask before git init; never init unprompted.
- Report PR link.

## Escalation Ladder
- A) Normal: Bishop plans, Knight implements, both on their configured models.
- B) On CONFIRMED capacity failure (HTTP 429, rate-limit message, quota error): retry worker once after retry window, then hand unfinished task to Mercenary with a verified free model, passing plan, repo state, completed work, verbatim errors, remaining criteria; no parallel workers on same files.
- C) Paid Muse Spark escalation only when free path stalled (Mercenary tried >=2 distinct free models, or >=3 consecutive capacity failures across Bishop/Knight/Mercenary); state reason if earlier; route planning to grand-bishop, implementation to knight-commander.
- D) Mage (opencode-go/glm-5.3) only for genuinely hard reasoning/refactoring/debugging after free attempts, or when paid Muse Spark path cannot solve it.
- Error classification: auth, permission, missing-model, config, network/server without rate-limit evidence, and real test failures are NOT capacity failures; diagnose first. Never fall back to paid silently. Record paid invocations with agent id, model id, reason, work, tests.
