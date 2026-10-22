/**
 * tailshare plugin — pure helpers (no opencode imports, no I/O).
 *
 * Everything in here is deliberately dependency-free so it can be unit-tested
 * with plain `node --test` and safely imported from the plugin entrypoint.
 */

/** Tool IDs whose results may carry command output. Allowlist, not blocklist:
 *  every other tool (read, write, edit, patch, glob, grep, webfetch, …)
 *  carries file content or fetched data that must never be rewritten. */
export const SHELL_TOOLS = new Set(["shell", "execute"]);

/** Short system-prompt hint (Mechanism A). Kept to two lines: it is appended
 *  to every agent-loop model call, so token cost matters. */
export const SYSTEM_HINT =
  "Share local-service links tailnet-reachably: expose the port with `tailshare share <port>` and share the URL it prints; " +
  "never report localhost/127.0.0.1/0.0.0.0/[::1] URLs as the link.";

/** Substring identifying the hint, used to keep injection idempotent. */
export const HINT_MARKER = "tailshare share <port>";

/** Annotation appended after a loopback URL that has no live serve mapping.
 *  It deliberately does NOT substitute a tailnet-IP URL: a loopback-only
 *  service would be unreachable there, and fabricating it would violate the
 *  repo's "Never invent URLs" rule. */
export const localOnlyNote = (port) => `(local-only; run: tailshare share ${port})`;

/**
 * Whether result rewriting (Mechanism B) — and, when false, the whole plugin —
 * is enabled. `TAILSHARE_OFF=1` (or "true") opts out via the environment;
 * `options.tailshareOff` / `options.off` opt out via the object form of the
 * `plugins` config entry.
 */
export function isRewritingEnabled(env = {}, options = {}) {
  const off = env && (env.TAILSHARE_OFF === "1" || String(env.TAILSHARE_OFF || "").toLowerCase() === "true");
  if (off) return false;
  if (options && (options.tailshareOff === true || options.off === true)) return false;
  return true;
}

/**
 * Idempotently ensure the tailshare hint is present in a context-hook system
 * array. Returns true when it pushed the hint, false when already present.
 */
export function ensureSystemHint(system) {
  if (!Array.isArray(system)) return false;
  for (const part of system) {
    if (part && typeof part.text === "string" && part.text.includes(HINT_MARKER)) return false;
  }
  system.push({ type: "text", text: SYSTEM_HINT });
  return true;
}

const LOOPBACK_NAMES = new Set(["localhost", "0.0.0.0", "::1", "::", "[::1]", "[::]"]);

/** True for loopback hosts: localhost, 0.0.0.0, 127/8, ::1. */
export function isLoopbackHost(host) {
  if (!host) return false;
  let h = String(host).toLowerCase();
  if (h.endsWith(".")) h = h.slice(0, -1);
  if (h.startsWith("[") && h.endsWith("]")) h = h.slice(1, -1);
  if (LOOPBACK_NAMES.has(h)) return true;
  if (/^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
  if (h === "::ffff:127.0.0.1") return true;
  return false;
}

/** True for URLs that are already tailnet-reachable: MagicDNS hosts,
 *  Tailscale CGNAT IPs, and Tailscale ULAs. */
export function isTailnetHost(host) {
  if (!host) return false;
  let h = String(host).toLowerCase();
  if (h.endsWith(".")) h = h.slice(0, -1);
  if (h.startsWith("[") && h.endsWith("]")) h = h.slice(1, -1);
  if (/\.ts\.net$/.test(h)) return true;
  const m = h.match(/^100\.(\d+)\.(\d+)\.(\d+)$/);
  if (m) {
    const b = Number(m[1]);
    if (b >= 64 && b <= 127) return true;
  }
  if (/^fd7a:115c:a1e0:/i.test(h)) return true;
  return false;
}

// Candidate http(s) URLs. Deliberately narrow: only http/https schemes, so
// t3-thread://, file://, data:, and other schemes can never match.
const URL_RE = /\bhttps?:\/\/[^\s"'`<>{}|\\^]+/g;

// Trailing punctuation that is prose, not URL (markdown links, sentences).
const TRAIL_RE = /[.,;!)}\]]+$/;

/**
 * Rewrite loopback URLs in free text.
 *
 * @param {string} text free text from a tool result
 * @param {Record<number,string>} portToUrl live serve map: local target port
 *   -> public base URL (no trailing slash), built ONLY from real
 *   `tailscale serve status` mappings. Ports with no entry are never
 *   substituted — the URL is left intact with a short annotation.
 * @returns {{ text: string, replaced: number, annotated: number }}
 */
export function rewriteText(text, portToUrl = {}) {
  if (typeof text !== "string" || text.length === 0) return { text, replaced: 0, annotated: 0 };
  let replaced = 0;
  let annotated = 0;
  const out = text.replace(URL_RE, (raw, offset, whole) => {
    let url = raw;
    let trail = "";
    const m = url.match(TRAIL_RE);
    if (m) {
      trail = m[0];
      url = url.slice(0, -trail.length);
    }
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return raw;
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return raw;
    if (!isLoopbackHost(parsed.hostname)) return raw; // public / already-tailnet: untouched
    const port = parsed.port ? Number(parsed.port) : parsed.protocol === "https:" ? 443 : 80;
    if (!Number.isInteger(port) || port < 1 || port > 65535) return raw;
    const base = portToUrl[port];
    if (base) {
      replaced += 1;
      // base never ends with "/"; parsed suffix always starts with "/" (at
      // least the root path), so plain concatenation is correct for both root
      // mappings (https://host + /sub) and path mappings (https://host/p + /sub).
      const suffix = (parsed.pathname || "") + (parsed.search || "") + (parsed.hash || "");
      return base + suffix + trail;
    }
    // No live mapping: leave intact, annotate once (idempotent — never stack).
    const after = whole.slice(offset + raw.length);
    if (after.startsWith(` ${localOnlyNote(port)}`)) return raw;
    annotated += 1;
    return url + ` ${localOnlyNote(port)}` + trail;
  });
  return { text: out, replaced, annotated };
}

/**
 * Build the live serve map from parsed `tailscale serve status --json` output.
 * Only real mappings are recorded: Web entry "<serveHost>:<servePort>",
 * handler <servePath> proxying a loopback target. Anything else is ignored.
 *
 * @param {any} status parsed JSON (or null/garbage -> {})
 * @returns {Record<number,string>} local target port -> public base URL
 */
export function buildServeMap(status) {
  /** @type {Record<number, Array<{servePort:number, servePath:string, serveHost:string}>>} */
  const byTarget = {};
  try {
    const web = (status && status.Web) || {};
    for (const key of Object.keys(web)) {
      const idx = key.lastIndexOf(":");
      if (idx < 0) continue;
      const serveHost = key.slice(0, idx).replace(/\.$/, "");
      const servePort = Number(key.slice(idx + 1));
      if (!serveHost || !Number.isInteger(servePort)) continue;
      const handlers = (web[key] && web[key].Handlers) || {};
      for (const servePath of Object.keys(handlers)) {
        const proxy = handlers[servePath] && handlers[servePath].Proxy;
        if (typeof proxy !== "string" || proxy.length === 0) continue;
        let target;
        try {
          target = new URL(proxy);
        } catch {
          continue;
        }
        if (!isLoopbackHost(target.hostname)) continue; // serve of a remote URL: not ours
        const targetPort = target.port ? Number(target.port) : target.protocol === "https:" ? 443 : 80;
        if (!Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) continue;
        (byTarget[targetPort] ||= []).push({ servePort, servePath, serveHost });
      }
    }
  } catch {
    return {};
  }
  /** @type {Record<number,string>} */
  const map = {};
  for (const targetPort of Object.keys(byTarget)) {
    const cands = byTarget[targetPort].slice().sort((a, b) => {
      const ap = a.servePort === 443 ? 0 : 1;
      const bp = b.servePort === 443 ? 0 : 1;
      if (ap !== bp) return ap - bp;
      if (a.servePort !== b.servePort) return a.servePort - b.servePort;
      return a.servePath.length - b.servePath.length;
    });
    const best = cands[0];
    const cleanPath = best.servePath === "/" ? "" : best.servePath;
    map[targetPort] =
      best.servePort === 443
        ? `https://${best.serveHost}${cleanPath}`
        : `https://${best.serveHost}:${best.servePort}${cleanPath}`;
  }
  return map;
}

/**
 * Read live serve state via an injected spawn function (same code path the
 * plugin uses; a missing/unusable `tailscale` makes spawn throw).
 *
 * @param {() => string} spawn returns `tailscale serve status --json` stdout; throws on any failure
 * @returns {{ ok: boolean, map: Record<number,string> }} ok:false means state
 *   is UNKNOWN — the caller must skip rewriting entirely (never annotate).
 */
export function readServeMap(spawn) {
  try {
    const raw = spawn();
    return { ok: true, map: buildServeMap(JSON.parse(raw)) };
  } catch {
    return { ok: false, map: {} };
  }
}

/**
 * Rewrite one completed tool result (pure). Returns the original reference
 * when nothing changed or the tool is not allowlisted.
 *
 * @param {string} tool tool ID (exact match against SHELL_TOOLS)
 * @param {any} result tool result ({ output?, content?, metadata? })
 * @param {Record<number,string>} portToUrl live serve map
 * @param {{ enabled?: boolean }} opts pass { enabled:false } under opt-out
 */
export function rewriteResult(tool, result, portToUrl = {}, opts = {}) {
  if (opts.enabled === false) return result;
  if (!SHELL_TOOLS.has(tool)) return result;
  if (!result || typeof result !== "object") return result;
  let changed = false;
  const next = Array.isArray(result) ? result.slice() : { ...result };
  if (typeof next.content === "string") {
    const r = rewriteText(next.content, portToUrl);
    if (r.replaced > 0 || r.annotated > 0) {
      next.content = r.text;
      changed = true;
    }
  } else if (Array.isArray(next.content)) {
    const parts = next.content.map((part) => {
      if (part && part.type === "text" && typeof part.text === "string") {
        const r = rewriteText(part.text, portToUrl);
        if (r.replaced > 0 || r.annotated > 0) {
          changed = true;
          return { ...part, text: r.text };
        }
      }
      return part; // file parts and anything else: byte-identical
    });
    if (changed) next.content = parts;
  }
  if (typeof next.output === "string") {
    const r = rewriteText(next.output, portToUrl);
    if (r.replaced > 0 || r.annotated > 0) {
      next.output = r.text;
      changed = true;
    }
  }
  return changed ? next : result;
}
