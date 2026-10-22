# Token budget

Measured with `scripts/measure-tokens.mjs` (chars and chars/4 tokens).

## Before / after

| Metric | Before | After |
|---|---:|---:|
| AGENTS.md | 9,044 | 5,189 |
| build system | 9,451 | 742 |
| monarch system | 9,451 | 742 |
| total agent systems | 24,306 | 4,832 |
| skill descriptions | 4,090 | 1,547 |

Scheduled prompt: revision 1 = 12,351 chars, revision 2 = 13,844 chars,
revision 3 = 7,859 bytes / 7,855 chars (`docs/scheduled-daily-model-check/after.txt`).

## Per-session estimate

A build session injects AGENTS.md plus the build system prompt:
before ~18,495 chars (~4,624 tokens), after ~5,931 chars (~1,483 tokens).

A worker session injects AGENTS.md plus the Mercenary system prompt:
before ~9,603 chars (~2,401 tokens), after ~5,607 chars (~1,402 tokens).

## Moved out of the injected context

Escalation policy, routing, depth, and incident history now live in
`docs/hierarchy-model.md` instead of being repeated in every system prompt.
AGENTS.md carries the normative rules once.

## Enforced budgets

`test/token-budget.test.mjs` (node:test) asserts:
- AGENTS.md <= 5,200 chars
- build and monarch systems <= 1,500 chars each
- sum of all agent system strings <= 5,000 chars
- total skill descriptions <= 2,000 chars

## Re-measure

```sh
node scripts/measure-tokens.mjs
```
