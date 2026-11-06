# Token budget

Context an agent pays for on every session, measured with
`scripts/measure-tokens.mjs` (chars and chars/4 tokens).

## What is measured

| What | Why it matters |
|---|---|
| `AGENTS.md` | rides every session's context |
| each agent `system` string | only when the repo ships `opencode.base.json` |
| skill frontmatter blocks | the skill catalog is injected on every session |
| skill descriptions | the part of the catalog an agent actually reads when choosing |

A missing optional input is reported as an empty figure rather than aborting
the run, so the script works in a repo that carries only rules and skills.

## Current figures

| Metric | Chars | ~Tokens |
|---|---:|---:|
| `AGENTS.md` | 5,112 | 1,278 |
| skill frontmatter (36 skills) | 13,718 | 3,430 |
| skill descriptions | 5,592 | 1,398 |

## Enforced budgets

`test/token-budget.test.mjs` (node:test, run by `npm test`) asserts:

- `AGENTS.md` <= 5,200 chars
- total skill descriptions <= 6,000 chars
- every `skills/*/SKILL.md` has a `name` and a `description`

The caps are guardrails against slow growth, not targets. Raise one
deliberately, in the same commit that needs the extra room.

## Re-measure

```sh
node scripts/measure-tokens.mjs   # or: npm run budget
```
