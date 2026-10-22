import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  SHELL_TOOLS,
  SYSTEM_HINT,
  HINT_MARKER,
  buildServeMap,
  ensureSystemHint,
  isLoopbackHost,
  isRewritingEnabled,
  localOnlyNote,
  readServeMap,
  rewriteResult,
  rewriteText,
} from "../plugins/tailshare/rewrite.mjs";

// Fixture shaped exactly like live `tailscale serve status --json` on the
// dev machine (host.tailnet-example.ts.net:443, "/" -> 127.0.0.1:3773,
// plus a path-routed mapping for port 8080).
const SERVE_JSON = JSON.stringify({
  TCP: { 443: { HTTPS: true } },
  Web: {
    "host.tailnet-example.ts.net:443": {
      Handlers: {
        "/": { Proxy: "http://127.0.0.1:3773" },
        "/p8080": { Proxy: "http://127.0.0.1:8080" },
      },
    },
    "host.tailnet-example.ts.net:8443": {
      Handlers: {
        "/": { Proxy: "http://127.0.0.1:9090" },
      },
    },
  },
});

const MAP = buildServeMap(JSON.parse(SERVE_JSON));

describe("buildServeMap", () => {
  it("maps target ports to public base URLs, preferring 443", () => {
    assert.equal(MAP[3773], "https://host.tailnet-example.ts.net");
    assert.equal(MAP[8080], "https://host.tailnet-example.ts.net/p8080");
    assert.equal(MAP[9090], "https://host.tailnet-example.ts.net:8443");
  });

  it("returns {} for garbage input instead of throwing", () => {
    assert.deepEqual(buildServeMap(null), {});
    assert.deepEqual(buildServeMap({}), {});
    assert.deepEqual(buildServeMap({ Web: null }), {});
  });

  it("ignores serve mappings whose target is not loopback", () => {
    const m = buildServeMap({
      Web: { "h.ts.net:443": { Handlers: { "/": { Proxy: "http://10.0.0.5:3000" } } } },
    });
    assert.deepEqual(m, {});
  });
});

describe("rewriteText with a live serve mapping", () => {
  it("rewrites a mapped loopback URL to the tailnet URL", () => {
    const r = rewriteText("try http://127.0.0.1:3773/ for the preview", MAP);
    assert.equal(r.text, "try https://host.tailnet-example.ts.net/ for the preview");
    assert.equal(r.replaced, 1);
  });

  it("preserves sub-paths, query, and fragment on path-routed mappings", () => {
    const r = rewriteText("open http://localhost:8080/app?x=1#top end", MAP);
    assert.equal(r.text, "open https://host.tailnet-example.ts.net/p8080/app?x=1#top end");
  });

  it("rewrites 0.0.0.0, [::1], and 127.x hosts when the port is mapped", () => {
    for (const h of ["0.0.0.0", "[::1]", "127.0.0.2"]) {
      const r = rewriteText(`at http://${h}:3773/`, MAP);
      assert.ok(r.text.includes("https://host.tailnet-example.ts.net/"), h);
    }
  });

  it("strips prose punctuation without swallowing it", () => {
    const r = rewriteText("(see http://127.0.0.1:3773/).", MAP);
    assert.equal(r.text, "(see https://host.tailnet-example.ts.net/).");
  });
});

describe("rewriteText without a serve mapping", () => {
  it("leaves the URL intact and annotates (never fabricates an IP URL)", () => {
    const r = rewriteText("dev at http://127.0.0.1:5555/ now", MAP);
    assert.equal(r.text, `dev at http://127.0.0.1:5555/ ${localOnlyNote(5555)} now`);
    assert.ok(!r.text.includes("100."), "must not invent a tailnet-IP URL");
    assert.equal(r.annotated, 1);
  });

  it("is idempotent: a second pass changes nothing", () => {
    const once = rewriteText("dev at http://127.0.0.1:5555/ now", MAP);
    const twice = rewriteText(once.text, MAP);
    assert.equal(twice.text, once.text);
    assert.equal(twice.annotated, 0);
  });
});

describe("annotation format (single parentheses, pinned)", () => {
  it("renders exactly one pair of parentheses around the note", () => {
    const r = rewriteText("http://127.0.0.1:49374/", new Map());
    assert.equal(r.text, "http://127.0.0.1:49374/ (local-only; run: tailshare share 49374)");
    assert.equal(r.annotated, 1);
  });

  it("an already-annotated loopback URL is returned unchanged", () => {
    const once = rewriteText("http://127.0.0.1:49374/", new Map()).text;
    const twice = rewriteText(once, new Map());
    assert.equal(twice.text, once);
    assert.equal(twice.annotated, 0);
    assert.ok(!twice.text.includes("(("), "must not stack a second note");
  });

  it("a serve-mapped loopback URL yields the tailnet URL with no annotation", () => {
    const r = rewriteText("see http://127.0.0.1:3773/ ok", MAP);
    assert.equal(r.text, "see https://host.tailnet-example.ts.net/ ok");
    assert.ok(!r.text.includes("local-only"), "mapped URLs carry no annotation");
  });
});

describe("rewriteText leaves other URLs alone", () => {
  it("does not touch public URLs", () => {
    const s = "docs at https://example.com:8443/a and http://93.184.216.34:3000/b";
    assert.equal(rewriteText(s, MAP).text, s);
  });

  it("does not touch already-tailnet URLs (MagicDNS, 100.x, ULA)", () => {
    for (const u of [
      "https://host.tailnet-example.ts.net/p8080",
      "http://" + ["100", "64", "0", "1"].join(".") + ":3000/",
      "http://[fd7a:115c:a1e0::1]:3000/",
    ]) {
      assert.equal(rewriteText(`see ${u} ok`, MAP).text, `see ${u} ok`, u);
    }
  });

  it("does not touch non-http schemes", () => {
    const s = "open t3-thread://abc, file:///tmp/x and data:text/plain,hi";
    assert.equal(rewriteText(s, MAP).text, s);
  });
});

describe("rewriteResult tool gating", () => {
  const mapped = { content: "up at http://127.0.0.1:3773/" };

  it("rewrites shell and execute results", () => {
    for (const tool of ["shell", "execute"]) {
      const out = rewriteResult(tool, { ...mapped }, MAP);
      assert.ok(out.content.includes("https://host.tailnet-example.ts.net/"), tool);
    }
  });

  it("never rewrites write/edit/patch/read results (same reference)", () => {
    for (const tool of ["write", "edit", "patch", "read"]) {
      const input = { content: "see http://127.0.0.1:3773/ in file" };
      assert.equal(rewriteResult(tool, input, MAP), input, tool);
    }
  });

  it("never rewrites any other tool (glob, grep, webfetch, subagent, MCP…)", () => {
    for (const tool of ["glob", "grep", "webfetch", "websearch", "subagent", "question", "skill", "beeper_query"]) {
      const input = { content: "http://127.0.0.1:3773/" };
      assert.equal(rewriteResult(tool, input, MAP), input, tool);
    }
  });

  it("rewrites text content parts but leaves file parts byte-identical", () => {
    const file = { type: "file", uri: "file:///x.png", mime: "image/png" };
    const out = rewriteResult(
      "shell",
      { content: [{ type: "text", text: "at http://127.0.0.1:3773/" }, file] },
      MAP,
    );
    assert.ok(out.content[0].text.includes("https://host.tailnet-example.ts.net/"));
    assert.equal(out.content[1], file);
  });

  it("returns the same reference when nothing changed", () => {
    const input = { content: "nothing to do here https://example.com/" };
    assert.equal(rewriteResult("shell", input, MAP), input);
  });

  it("respects the enabled:false opt-out", () => {
    const input = { ...mapped };
    assert.equal(rewriteResult("shell", input, MAP, { enabled: false }), input);
  });
});

describe("opt-out", () => {
  it("TAILSHARE_OFF=1 disables rewriting", () => {
    assert.equal(isRewritingEnabled({ TAILSHARE_OFF: "1" }, {}), false);
  });

  it("options flags disable rewriting", () => {
    assert.equal(isRewritingEnabled({}, { tailshareOff: true }), false);
    assert.equal(isRewritingEnabled({}, { off: true }), false);
  });

  it("enabled by default", () => {
    assert.equal(isRewritingEnabled({}, {}), true);
    assert.equal(isRewritingEnabled({ PATH: "/usr/bin" }, {}), true);
  });
});

describe("ensureSystemHint", () => {
  it("pushes one short text block, never duplicates", () => {
    const system = [];
    assert.equal(ensureSystemHint(system), true);
    assert.equal(system.length, 1);
    assert.equal(system[0].type, "text");
    assert.ok(system[0].text.includes(HINT_MARKER));
    assert.ok(SYSTEM_HINT.split("\n").length <= 2, "keep it to one or two lines");
    assert.equal(ensureSystemHint(system), false);
    assert.equal(system.length, 1);
  });

  it("treats an equivalent pre-existing rule as present", () => {
    const system = [{ type: "text", text: "from AGENTS.md: use `tailshare share <port>` for links" }];
    assert.equal(ensureSystemHint(system), false);
    assert.equal(system.length, 1);
  });
});

describe("readServeMap failure semantics", () => {
  it("ok:true with the map when tailscale answers", () => {
    const r = readServeMap(() => SERVE_JSON);
    assert.equal(r.ok, true);
    assert.equal(r.map[3773], "https://host.tailnet-example.ts.net");
  });

  it("ok:false (skip rewriting entirely) when tailscale is missing/broken", () => {
    for (const spawn of [
      () => {
        throw Object.assign(new Error("spawn tailscale ENOENT"), { code: "ENOENT" });
      },
      () => "not json{",
      () => {
        throw new Error("timed out");
      },
    ]) {
      const r = readServeMap(spawn);
      assert.equal(r.ok, false);
      assert.deepEqual(r.map, {});
    }
  });
});

describe("host classifiers", () => {
  it("loopback", () => {
    for (const h of ["localhost", "LOCALHOST.", "127.0.0.1", "127.4.5.6", "0.0.0.0", "::1", "[::1]"]) {
      assert.equal(isLoopbackHost(h), true, h);
    }
    assert.equal(isLoopbackHost("example.com"), false);
    const cgnatIp = ["100", "64", "0", "1"].join(".");
  assert.equal(isLoopbackHost(cgnatIp), false);
  });

  it("SHELL_TOOLS contains exactly the command-execution tools", () => {
    assert.deepEqual([...SHELL_TOOLS].sort(), ["execute", "shell"]);
  });
});
