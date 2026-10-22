# P8 provider

P8 is the preferred provider for every selectable model slot in agent-kit.
It outranks opencode free and paid Go models.

## Providers

- `p8-agent` — "P8 reliable agents", base URL `https://ollama.ecitis.org/agent/v1`.
- `p8-agent-fast` — responsive variant, base URL `https://ollama.ecitis.org/agent-fast/v1`.

Both are OpenAI-compatible. Key: `{file:~/.config/opencode/secrets/p8-api-key}`
(mode 600). Timeout: 180000 ms. User-agent header `opencode`. Compatibility:
`reasoningField: reasoning`, `maxTokensField: max_tokens`,
`requireFinishReason: true`. The user states both providers are unmetered with
no token limit.

## Roster (declared in opencode.base.json)

`router/agent`, `router/agent-fast`, `beam`, `kimi-k3:cloud`,
`glm-5.3:cloud`, `nemotron-3-ultra` (also `:cloud`),
`muse-spark-open:cloud`, `gemma4:31b-cloud`, `qwen3.6`,
`qwen3.6:27b-coding`, `qwen3.6:latest`, `qwen3.8-flash-next:125b-mlx`.
Display names carry a `p8 ` prefix (model ids unchanged); the two router entries keep their existing `P8 automatic ...` display names.

## P8-first policy

p8 outranks opencode free and paid Go for any selectable slot. Role ladders
(try the next model on a failed fitness call):

- Implementation/build: `p8-agent/deepseek-v4-pro`, `p8-agent/gemma4:31b`,
  `p8-agent/nemotron-3-ultra`, `p8-agent/kimi-k3:cloud`,
  `p8-agent/qwen3.6:27b-coding`, `p8-agent/router/agent`.
- Planning/audit: `p8-agent/deepseek-v4-pro`, `p8-agent/nemotron-3-ultra`,
  `p8-agent/kimi-k3:cloud`, `p8-agent/router/agent`.
- Default/general: `p8-agent/deepseek-v4-pro`, then `p8-agent/gemma4:31b`.

## Failure handling

- A failed fitness call moves to the next model in the ladder.
- A confirmed HTTP 429 / rate-limit / quota error on p8 is a capacity failure
  to report, not proof the provider is broken.
- Never pin an unverified p8 model.
- Paid escalation IDs stay exactly as declared and are never silently replaced:
  Mage `opencode-go/glm-5.3`, Witch `opencode-go/deepseek-v4-pro`
  (fallback `opencode-go/gpt-5.6-luna`), Grand Bishop and Knight Commander
  `opencode-go/muse-spark-1.3-contributor`.

## Verification recipe (bounded, read-only)

Read the key inline and never echo it:

```sh
key=$(cat ~/.config/opencode/secrets/p8-api-key)
curl -sS -H "Authorization: Bearer $key" \
  https://ollama.ecitis.org/agent/v1/models >/dev/null
```

Use a bounded timeout per candidate and at most two attempts. Treat 401/404 as
unavailable, 429 as capacity.

## Per-host key caveat

The key is per host. Confirm
`~/.config/opencode/secrets/p8-api-key` exists (mode 600) on every host that
receives the roster before pinning a p8 model. Pi uses a separate
`P8_ROUTER_API_KEY` in `~/.pi/agent/key.env`.

## Pi provider config (in-repo)

The Pi harness config lives in this repo at `pi/` (single source of truth;
see `docs/PI_CONFIG.md`). `pi/models.json` declares the same two providers for
Pi — `p8-agent` (`https://ollama.ecitis.org/agent/v1`) and `p8-agent-fast`
(`/agent-fast/v1`) — with the key referenced only as `$P8_ROUTER_API_KEY`;
the secret itself lives at `~/.pi/agent/key.env` (mode 600), created from
`pi/key.env.example`. Install on a fresh device via `scripts/bootstrap.sh`.

Pi defaults on installed hosts:

- Provider: `p8-agent`, model `kimi-k3:cloud`.
- Enabled models: `p8-agent/kimi-k3:cloud`, `p8-agent/router/agent`.
- Config dir: `~/.pi/agent/` (`models.json`, `settings.json`, `key.env`,
  `p8-launch.py`). Launcher injects the key; `models.json` references
  `$P8_ROUTER_API_KEY` only.

## Roster status and pins (2026-10-22)

OpenCode declares 50 models on `p8-agent` (+ 26 on `p8-agent-fast`) in
`opencode.base.json`; Pi catalogs are capped at 32 routed IDs. Live catalog
discovery was re-confirmed reachable on this date. Current pin decisions
(enforced by `plugins/hierarchy-guard/policy.mjs`):

- bishop, knight → `p8-agent/deepseek-v4-pro`
- p8-worker → `p8-agent/gemma4:31b`
- mercenary (free fallback) → `p8-agent/nemotron-3-ultra`
- Pi default → `p8-agent/kimi-k3:cloud` (currently healthy)

`beam` and `minimax-m3.1-flash` are currently down (503); do not pin or rely
on them until a probe passes.

## Observed behaviour

On 2026-10-21, p8 subagent calls hit rate limits. A confirmed 429 is a
capacity failure to report and retry with the next ladder model; it is not
proof the provider is broken. Do not treat it as an auth or config failure.
