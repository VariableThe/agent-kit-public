/**
 * tailshare — OpenCode plugin (V2 API, verified against opencode v2.0.29).
 *
 * Two mechanisms, both fail-safe (never throw; a machine without tailscale
 * behaves exactly as if the plugin were absent):
 *
 *  Mechanism A — system-prompt injection: `session.hook("context")` appends
 *  one short text block telling the model to share local-service links
 *  tailnet-reachably. Also registered on `compaction` so the guidance
 *  survives checkpoint summaries.
 *
 *  Mechanism B — tool-result rewrite: `tool.hook("execute.after")` rewrites
 *  loopback URLs in SHELL tool results only (`shell`, `execute`). A tailnet
 *  URL is emitted ONLY for a port with a real, live `tailscale serve`
 *  mapping; otherwise the URL is left intact with a short annotation.
 *
 * Explicit non-goal: free-form assistant prose cannot be rewritten — v2 has
 * no such hook (the stale V1 `@opencode-ai/plugin` types suggest
 * `experimental.text.complete` / `chat.message`, but the v2.0.29 binary never
 * calls them). The AGENTS.md rule covers assistant prose instead.
 */
import { Plugin } from "@opencode/plugin";
import { execFileSync } from "node:child_process";
import {
  ensureSystemHint,
  isRewritingEnabled,
  readServeMap,
  rewriteResult,
} from "./rewrite.mjs";

// Plugin-scoped storage key for the cached serve map (+ timestamp).
const STORE_KEY = "tailshare.serve.v1";
// Serve-state TTL: fresh enough to track `tailshare share` changes, stale
// enough to avoid spawning `tailscale` on every tool result.
const CACHE_TTL_MS = 60_000;

function spawnServeStatus(): string {
  const raw = execFileSync("tailscale", ["serve", "status", "--json"], {
    timeout: 5_000,
    stdio: ["ignore", "pipe", "ignore"],
  });
  return raw.toString("utf8");
}

export default Plugin.define({
  id: "tailshare",
  async setup(ctx) {
    try {
      const enabled = isRewritingEnabled(process.env, ctx.options ?? {});
      if (!enabled) return; // TAILSHARE_OFF=1 or options flag: do nothing at all.

      // --- Mechanism A: one short hint on every agent-loop model call. ---
      const injectHint = (event: { system?: unknown }) => {
        try {
          if (event && Array.isArray(event.system)) ensureSystemHint(event.system);
        } catch {
          /* never break the agent loop */
        }
      };
      await ctx.session.hook("context", injectHint);
      await ctx.session.hook("compaction", injectHint);

      // --- Mechanism B: rewrite loopback URLs in shell tool results. ---
      await ctx.tool.hook("execute.after", async (event) => {
        try {
          if (event.status !== "completed") return;
          const now = Date.now();
          let cached: unknown = undefined;
          try {
            cached = await ctx.storage.get(STORE_KEY);
          } catch {
            cached = undefined;
          }
          let map: Record<number, string> | undefined;
          if (
            cached &&
            typeof cached === "object" &&
            typeof (cached as { ts?: unknown }).ts === "number" &&
            now - (cached as { ts: number }).ts < CACHE_TTL_MS &&
            (cached as { map?: unknown }).map &&
            typeof (cached as { map?: unknown }).map === "object"
          ) {
            map = (cached as { map: Record<number, string> }).map;
          } else {
            // Same code path as the unit-tested readServeMap: a missing or
            // broken `tailscale` yields ok:false, and we skip rewriting
            // entirely (not even annotations) — unknown state, not empty.
            const live = readServeMap(spawnServeStatus);
            if (!live.ok) return;
            map = live.map;
            try {
              await ctx.storage.set(STORE_KEY, { ts: now, map });
            } catch {
              /* cache is best-effort */
            }
          }
          const next = rewriteResult(event.tool, event.result, map ?? {}, { enabled: true });
          if (next !== event.result) event.result = next;
        } catch {
          /* fail safe: corrupted output is worse than no rewrite */
        }
      });
    } catch {
      /* setup must never throw: without tailscale the plugin is a no-op */
    }
  },
});
