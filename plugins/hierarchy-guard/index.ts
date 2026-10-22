/**
 * hierarchy-guard — OpenCode policy-enforcement plugin (verified against
 * opencode v2.0.29).
 *
 * WHAT IT ENFORCES
 * Every agent's configured `permissions` rules are re-evaluated by the plugin
 * AFTER the runtime resolves its own rules, and a `deny` is forced when the
 * agent's policy denies the action. Client layers (T3 Code, an SDK
 * `session.update({ permissions })`, or anything else) append session-level
 * rules that win under the runtime's last-match-wins resolution — which is
 * exactly how read-only agents such as `bishop` were observed executing
 * shell/edit calls.
 *
 * PRECEDENCE (verified in the v2.0.29 permission gate)
 *   1. agent rules from config
 *   2. session rules appended by the client (win over agent rules)
 *   3. plugin `evaluate` handlers, which may only force `deny`
 * `permission.hook("evaluate")` is the one hook the runtime runs after rule
 * resolution, so this plugin can only tighten a decision, never loosen one: it
 * never upgrades `deny` -> `allow` or `ask` -> `allow`.
 *
 * FAIL-SAFE
 * Every path is wrapped: a missing/malformed config, or an unexpected event
 * shape, leaves the session exactly as it was and never breaks the agent loop.
 * Enforcement is the permission hook for gated tools, plus input neutering
 * (below) for the execute tool — except under a restricted executor (the T3
 * Code harness), where the catalog is kept for an agent whose policy allows
 * delegation. The `tool.hook("execute.before")` path for the shell tool itself
 * is audit-only: the shell path ignores input mutation, so that leg only logs.
 * Opt out with HIERARCHY_GUARD_OFF=1.
 *
 * SUBAGENT MODEL ROUTING
 * The native OpenCode `subagent` tool picks its child model as
 * `input.model ?? agent.model ?? parentSession.model`, so an omitted `model`
 * inherits the parent (Monarch/`build`) model. On
 * `tool.hook("execute.before")` for that tool, the child model is routed by the
 * agent's declared class in MODEL_ROUTING (policy.mjs): pinned workers are
 * corrected to their configured model, explicit workers may be given any
 * non-empty model, and paid escalation agents accept only their allowed paid
 * models. This is a corrective pin, not a deny. A target that the loaded policy
 * does not declare, or that may itself delegate, is neutered instead. T3's
 * app-owned `delegate_task` / `functions.subagent` do not fire this hook and are
 * prompt-enforced only — see the README.
 */
import { Plugin } from "@opencode/plugin";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  deriveCapabilities,
  loadAgentModels,
  loadPolicy,
  resolveEffect,
  resolveExecutor,
  resolveNestedDelegation,
  resolveSubagentInput,
  shouldNeuterExecute,
} from "./policy.mjs";

const OFF_ENV = "HIERARCHY_GUARD_OFF";
const TRACE_ENV = "HIERARCHY_GUARD_TRACE";

type EvaluateEvent = {
  sessionID?: unknown;
  agent?: unknown;
  action?: unknown;
  resources?: unknown;
  effect?: string;
  message?: string;
};

type ToolEvent = {
  sessionID?: unknown;
  agent?: unknown;
  tool?: unknown;
  input?: { command?: unknown; cmd?: unknown; code?: unknown } | unknown;
};

function isDisabled(env: NodeJS.ProcessEnv | undefined): boolean {
  return String(env?.[OFF_ENV] ?? "") === "1";
}

/** `OPENCODE_CONFIG` when opencode is pointed at an explicit file, else the global config. */
function resolveConfigPath(env: NodeJS.ProcessEnv | undefined): string {
  const explicit = env?.OPENCODE_CONFIG;
  if (typeof explicit === "string" && explicit.length > 0) return explicit;
  return join(homedir(), ".config", "opencode", "opencode.json");
}

function preview(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.length > 200 ? value.slice(0, 200) : value;
}

/** One short, factual line naming the agent, action and resource. */
function denyMessage(agent: unknown, action: unknown, resources: unknown, rule: unknown): string {
  const list = Array.isArray(resources) ? resources : [];
  const first = list.find((r) => typeof r === "string") as string | undefined;
  const subject = first === undefined ? "" : ` for ${JSON.stringify(preview(first))}`;
  const matched =
    rule && typeof rule === "object" && typeof (rule as { resource?: unknown }).resource === "string"
      ? ` (rule ${JSON.stringify((rule as { action?: unknown }).action)}/${JSON.stringify(
          (rule as { resource: string }).resource,
        )})`
      : "";
  return `hierarchy-guard: agent ${JSON.stringify(String(agent ?? ""))} is not permitted to use ${JSON.stringify(
    String(action ?? ""),
  )}${subject}${matched}`;
}

/** A single structured stderr line, emitted only when we actually deny. */
function logDenial(event: EvaluateEvent | ToolEvent, action: string, rule: unknown): void {
  try {
    const resources = Array.isArray((event as EvaluateEvent).resources)
      ? ((event as EvaluateEvent).resources as unknown[])
      : [];
    const input = (event as ToolEvent).input;
    if (input && typeof input === "object") {
      const command =
        (input as { command?: unknown }).command ??
        (input as { cmd?: unknown }).cmd ??
        (input as { code?: unknown }).code; // execute tool carries JS, not a command
      if (typeof command === "string") resources.push(command);
    }
    console.error(
      JSON.stringify({
        plugin: "hierarchy-guard",
        event: "deny",
        sessionID: typeof event.sessionID === "string" ? event.sessionID : undefined,
        agent: typeof event.agent === "string" ? event.agent : undefined,
        action,
        resources: resources.filter((r) => typeof r === "string").slice(0, 3).map(preview),
        rule:
          rule && typeof rule === "object" && typeof (rule as { resource?: unknown }).resource === "string"
            ? { action: (rule as { action?: unknown }).action, resource: (rule as { resource: string }).resource }
            : undefined,
      }),
    );
  } catch {
    /* logging must never break anything */
  }
}

/** A structured stderr line emitted when the subagent hook pins a model. */
function logModelPin(
  event: ToolEvent,
  result: { agent: string; expected: unknown; actual: unknown; reason?: string },
): void {
  try {
    console.error(
      JSON.stringify({
        plugin: "hierarchy-guard",
        event: "subagent-model-pin",
        sessionID: typeof event.sessionID === "string" ? event.sessionID : undefined,
        agent: typeof event.agent === "string" ? event.agent : undefined,
        tool: "subagent",
        subagent: result.agent,
        expected: result.expected,
        actual: result.actual,
        reason: result.reason,
      }),
    );
  } catch {
    /* logging must never break anything */
  }
}

/** One structured stderr line for a blocked nested delegation. */
function logBlockedDelegation(
  event: ToolEvent,
  result: { agent: string; tool: string; reason: string },
): void {
  try {
    console.error(
      JSON.stringify({
        plugin: "hierarchy-guard",
        event: "delegation-blocked",
        sessionID: typeof event.sessionID === "string" ? event.sessionID : undefined,
        agent: typeof event.agent === "string" ? event.agent : undefined,
        tool: result.tool,
        reason: result.reason,
      }),
    );
  } catch {
    /* logging must never break anything */
  }
}

/** One structured stderr line for a dispatch rejected on declared-policy grounds. */
function logBlockedDispatch(event: ToolEvent, result: { agent: string; reason: string }): void {
  try {
    console.error(
      JSON.stringify({
        plugin: "hierarchy-guard",
        event: "subagent-dispatch-blocked",
        sessionID: typeof event.sessionID === "string" ? event.sessionID : undefined,
        agent: typeof event.agent === "string" ? event.agent : undefined,
        tool: "subagent",
        target: result.agent,
        reason: result.reason,
      }),
    );
  } catch {
    /* logging must never break anything */
  }
}

/**
 * Opt-in trace line (HIERARCHY_GUARD_TRACE=1): one per hook event, naming the
 * decision this plugin reached. Off by default and never changes behavior.
 */
function traceEvent(
  env: NodeJS.ProcessEnv | undefined,
  hook: "permission.evaluate" | "execute.before",
  event: EvaluateEvent | ToolEvent,
  action: unknown,
  effect: unknown,
): void {
  try {
    if (String(env?.[TRACE_ENV] ?? "") !== "1") return;
    const agent = (event as EvaluateEvent).agent;
    const tool = (event as ToolEvent).tool;
    console.error(
      JSON.stringify({
        plugin: "hierarchy-guard",
        event: "trace",
        hook,
        agent: typeof agent === "string" ? agent : undefined,
        tool: typeof tool === "string" ? tool : undefined,
        action: typeof action === "string" ? action : undefined,
        effect: typeof effect === "string" ? effect : undefined,
      }),
    );
  } catch {
    /* tracing must never break anything */
  }
}

export default Plugin.define({
  id: "hierarchy-guard",
  async setup(ctx) {
    try {
      if (isDisabled(process.env)) return;
      const configPath = resolveConfigPath(process.env);

      const rulesFor = (agent: unknown) => {
        if (typeof agent !== "string" || agent.length === 0) return undefined;
        return loadPolicy(configPath).get(agent);
      };

      // --- Primary mechanism: force deny after the runtime resolved rules. ---
      await ctx.permission?.hook?.("evaluate", (event: EvaluateEvent) => {
        try {
          if (!event || typeof event !== "object") return;
          const action = typeof event.action === "string" ? event.action : "";
          const rules = rulesFor(event.agent);
          if (!rules) {
            traceEvent(process.env, "permission.evaluate", event, action, "allow"); // unconfigured agent: untouched
            return;
          }
          const { effect, rule } = resolveEffect(action, event.resources, rules);
          if (effect === "deny") {
            event.effect = "deny";
            event.message = denyMessage(event.agent, event.action, event.resources, rule);
            logDenial(event, action, rule);
          }
          traceEvent(process.env, "permission.evaluate", event, action, effect);
        } catch {
          /* never break the agent loop */
        }
      });

      // --- execute tool: no permission gate in v2.0.29, so neuter on deny. ---
      // The execute (Code Mode JS) tool carries no `permission` field and
      // triggers no permission.evaluate events, yet on a host executor its
      // sandbox exposes require()/child_process. Gate it as shell-equivalent:
      // when the agent's policy denies unconstrained shell, replace the tool
      // input with an inert payload, unless the agent may delegate under a
      // restricted executor. Verified 2026-10-17 against
      // v2.0.29: the execute path honors the replacement (the tool errors
      // with "Code cannot be empty." and no JS runs), while the shell
      // tool ignores the same mutation — so the permission hook above
      // stays the enforcer for shell, and this replacement is the
      // enforcer for execute. Never returns a value; the hook contract
      // is void and throwing does not block.
      await ctx.tool?.hook?.("execute.before", (event: ToolEvent) => {
        let traceAction = typeof event?.tool === "string" ? event.tool : undefined;
        let traceEffect = "allow";
        try {
          if (!event || typeof event !== "object") return;
          if (event.tool === "execute") {
            traceAction = "shell"; // Code Mode is gated as shell-equivalent
            const rules = rulesFor(event.agent);
            if (!rules) return; // no configured policy: leave the session alone
            // Executor split (2026-10-21). Under a restricted executor (the T3
            // Code harness) the execute tool is a catalog bridge, not the general
            // JS runtime: it carries the browser, messaging, MCP and remote
            // sandbox tools, and that residual authority is explicitly NOT
            // governed here. The plugin applies the one enumerable proxy instead
            // of a name list: an agent whose policy denies delegation is
            // neutered (no second route to the same capability), and an agent
            // that may delegate keeps the catalog. On a host executor Code Mode
            // reaches the filesystem, so an agent without shell is always
            // neutered. The permission hook above stays the authoritative
            // enforcer for shell throughout.
            if (!shouldNeuterExecute(deriveCapabilities(rules), resolveExecutor(process.env))) return;
            const { rule } = resolveEffect("shell", "*", rules);
            try {
              (event as { input?: unknown }).input = { code: "" };
            } catch {
              /* fall through to the audit log */
            }
            logDenial(event, "shell", rule);
            traceEffect = "deny";
            return;
          }
          // --- subagent tool: route the child model by its declared class. ---
          // The native subagent tool resolves its child model as
          // `input.model ?? agent.model ?? parentSession.model` (opencode
          // v2.0.29 source), so an omitted model inherits the parent's model —
          // the exact bug this guards. Correct it from the config-loaded model
          // map. This same `execute.before` hook is what the subagent tool's own
          // internal normalization uses (`N.input = P`), so mutating the input
          // here is proven to reach the tool. T3's app-owned `delegate_task`
          // does not fire this hook and is prompt-enforced only (see README).
          if (event.tool === "subagent") {
            // Delegation depth guard, checked FIRST: only an agent whose
            // declared policy allows delegation may delegate. A worker that
            // reaches here is already a child, so a nested dispatch would
            // produce an identity the child cannot act with (delegate_task
            // inherits the caller's identity, so the grandchild runs as build
            // with edit/shell denied). Neuter the native call by replacing the
            // agent id with an unmatchable sentinel, then audit. T3 app-owned
            // tools are not hook-visible and are prompt-enforced only (see
            // AGENTS.md).
            const policy = loadPolicy(configPath);
            const nesting = resolveNestedDelegation(event.agent, event.tool, event.input, policy);
            if (nesting) {
              try {
                (event as { input?: unknown }).input = nesting.input;
              } catch {
                /* fall through to the audit log */
              }
              logBlockedDelegation(event, nesting);
              traceEffect = "deny";
              return;
            }
            const input = event.input;
            if (!input || typeof input !== "object") return;
            const result = resolveSubagentInput(input, loadAgentModels(configPath), policy);
            if (!result) return; // nothing declared to correct
            try {
              (event as { input?: unknown }).input = result.input;
            } catch {
              /* fall through to the audit log */
            }
            if (result.kind === "blocked") {
              logBlockedDispatch(event, result);
              traceEffect = "deny";
              return;
            }
            logModelPin(event, result);
            return;
          }
          if (event.tool !== "shell") return;
          const rules = rulesFor(event.agent);
          if (!rules) return;
          const input = event.input;
          const command =
            input && typeof input === "object"
              ? ((input as { command?: unknown }).command ?? (input as { cmd?: unknown }).cmd)
              : undefined;
          if (typeof command !== "string") return;
          const { effect } = resolveEffect("shell", command, rules);
          if (effect !== "deny") return;
          // Log only. Observed on v2.0.29: `execute.before` runs BEFORE the
          // permission check, so replacing `input.command` here DOES take
          // effect — and it makes the permission hook evaluate the placeholder
          // instead of the real command, masking the deny message. The
          // permission hook above is the authoritative, rules-exact gate, so
          // this path stays a non-mutating audit line.
          logDenial(event, "shell", null);
          traceEffect = "deny";
        } catch {
          /* non-throwing by design */
        } finally {
          traceEvent(process.env, "execute.before", event, traceAction, traceEffect);
        }
      });
    } catch {
      /* setup must never throw */
    }
  },
});
