/**
 * policy.mjs — pure, dependency-free policy helpers for the hierarchy-guard
 * plugin. No opencode imports: everything here runs under plain Node and is
 * unit-tested directly.
 *
 * The rule matcher is a faithful copy of opencode v2.0.29's `Q_(value, rule)`
 * (decompiled from /usr/bin/opencode):
 *
 *   function Q_(value, pattern) {
 *     const v = value.replaceAll(backslash, slash);
 *     let p = pattern.replaceAll(backslash, slash)
 *                    .replace(regex-metachars, escaped)   // [ . + ^ $ { } ( ) | [ ] \ ]
 *                    .replace(every-star, dot-star)
 *                    .replace(every-question, dot);
 *     if (p.endsWith(" .*")) p = p.slice(0, -3) + "( .*)?";
 *     return new RegExp("^" + p + "$", "s").test(v);
 *   }
 *
 * The plugin MUST agree with the runtime exactly: a matcher that differed would
 * over- or under-enforce relative to the rules the runtime applies on its own.
 * The trailing " .*" quirk is what makes `pwd *` (and every `cmd *` allow rule)
 * also match the bare command `pwd`.
 */

import { readFileSync, statSync } from "node:fs";

/** A regex that never matches, used for malformed patterns. */
const NEVER = /(?!)/;

/**
 * opencode normalizes backslashes to forward slashes in both the value and the
 * pattern before matching (Windows paths in policy resources).
 */
export function normalizeResource(value) {
  return String(value ?? "").replaceAll("\\", "/");
}

const patternCache = new Map();

/**
 * Compile a policy resource/action glob into an anchored dotAll RegExp that
 * behaves byte-for-byte like the runtime's matcher.
 */
export function compilePattern(resource) {
  if (typeof resource !== "string") return NEVER;
  const cached = patternCache.get(resource);
  if (cached !== undefined) return cached;

  let body = resource
    .replaceAll("\\", "/")
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  // Runtime quirk: a trailing " .*" becomes an OPTIONAL argument group, so a
  // `cmd *` rule also matches the bare `cmd`.
  if (body.endsWith(" .*")) body = body.slice(0, -3) + "( .*)?";

  const re = new RegExp("^" + body + "$", "s");
  patternCache.set(resource, re);
  return re;
}

/** True when `value` matches a single glob `pattern` (runtime semantics). */
export function matchesPattern(value, pattern) {
  return compilePattern(pattern).test(normalizeResource(value));
}

const KNOWN_EFFECTS = new Set(["allow", "ask", "deny"]);

/** A policy rule must at least name an action and a resource glob. */
export function isRule(rule) {
  return (
    !!rule &&
    typeof rule === "object" &&
    typeof rule.action === "string" &&
    typeof rule.resource === "string"
  );
}

/** Unknown/missing effects are treated as the runtime default, "ask". */
function ruleEffect(rule) {
  return rule && typeof rule.effect === "string" && KNOWN_EFFECTS.has(rule.effect)
    ? rule.effect
    : "ask";
}

/**
 * Resolve one resource against a rule list with the runtime's last-match-wins
 * `findLast` semantics. Returns the default `ask` when nothing matches.
 */
function resolveResource(action, resource, rules) {
  const value = normalizeResource(resource);
  const hit = rules.findLast(
    (rule) => compilePattern(rule.action).test(action) && compilePattern(rule.resource).test(value),
  );
  if (hit === undefined) return { effect: "ask", rule: null };
  return { effect: ruleEffect(hit), rule: hit };
}

/**
 * Resolve the effective effect for an action over several resources, mirroring
 * the runtime gate: deny as soon as any resource denies, otherwise ask if any
 * asks, otherwise allow.
 */
export function resolveEffect(action, resources, rules) {
  const list = (Array.isArray(rules) ? rules : []).filter(isRule);
  const actionValue = normalizeResource(action);
  const values = Array.isArray(resources) ? resources : [resources];

  let rule = null;
  let sawAsk = false;
  let sawAllow = false;

  for (const resource of values) {
    const resolved = resolveResource(actionValue, resource, list);
    if (resolved.effect === "deny") return { effect: "deny", rule: resolved.rule };
    if (resolved.effect === "ask") {
      sawAsk = true;
      if (rule === null) rule = resolved.rule;
    } else {
      sawAllow = true;
      if (rule === null) rule = resolved.rule;
    }
  }

  if (sawAsk) return { effect: "ask", rule };
  if (sawAllow) return { effect: "allow", rule };
  return { effect: "allow", rule: null }; // no resources at all: runtime yields allow
}

/* Capability derivation.
 * An agent's declared rules are the only source of truth for what it may do, so
 * the rules below are derived from the policy maps instead of parallel name
 * lists. `delegate` is true only when the policy explicitly allows dispatching
 * to at least one agent; a lone `subagent * deny` stays false because no target
 * resolves to `allow`. */

/** Derive what an agent may do from its rule list. `known:false` for non-arrays. */
export function deriveCapabilities(rules) {
  if (!Array.isArray(rules)) {
    return { known: false, write: false, execute: false, targets: [], delegate: false };
  }
  const write = resolveEffect("edit", "*", rules).effect !== "deny";
  const execute = resolveEffect("shell", "*", rules).effect !== "deny";
  const targets = rules
    .filter(
      (rule) =>
        isRule(rule) &&
        ruleEffect(rule) === "allow" &&
        compilePattern(rule.action).test("subagent") &&
        resolveEffect("subagent", rule.resource, rules).effect === "allow",
    )
    .map((rule) => rule.resource);
  return { known: true, write, execute, targets, delegate: targets.length > 0 };
}

/** True only when the policy explicitly allows dispatching to `target`. */
export function mayDispatchTo(rules, target) {
  if (typeof target !== "string" || target.length === 0) return false;
  return resolveEffect("subagent", target, rules).effect === "allow";
}

/**
 * Which executor the session runs under. The T3 Code harness is a restricted
 * interpreter — it cannot reach the filesystem or spawn a process — so
 * `shouldNeuterExecute` derives its exemption from the agent's declared policy
 * rather than from an agent-name list. Native OpenCode and any harness we do not
 * recognise is a host executor, where Code Mode reaches the filesystem.
 */
export function resolveExecutor(env) {
  return isT3CodeHarness(env) ? "restricted" : "host";
}

/**
 * Whether the execute tool must be neutered for an agent. Code Mode's catalog
 * exposure is the only enumerable proxy for its authority, so the guard denies
 * it exactly when the agent cannot execute and either runs on a host executor
 * (where JS reaches the filesystem) or cannot delegate (no alternate route to
 * the same capability). An agent that denies shell but may delegate — Monarch on
 * T3 Code — keeps the catalog.
 */
export function shouldNeuterExecute(caps, executor) {
  const execute = !!caps && caps.execute === true;
  const delegate = !!caps && caps.delegate === true;
  return !execute && (executor === "host" || !delegate);
}

/**
 * Decide whether `targetAgent` may be dispatched to. `policy` maps agent ->
 * rules, `models` maps agent -> configured model, and `requires` names the
 * capabilities the task needs. Every problem is collected as `{ code, reason }`
 * so a caller can report the whole picture; an empty list means allowed. All
 * inputs are normalised here, so this never throws.
 */
export function evaluateDispatch(args) {
  const violations = [];
  const { targetAgent, requires, policy, models } = args && typeof args === "object" ? args : {};
  const policyMap = policy instanceof Map ? policy : new Map();
  const modelMap = models instanceof Map ? models : new Map();
  const needs = requires && typeof requires === "object" ? requires : {};
  const name = typeof targetAgent === "string" ? targetAgent : "";

  if (!policyMap.has(name)) {
    violations.push({ code: "unknown-agent", reason: "unknown target agent " + JSON.stringify(name) });
  } else {
    const caps = deriveCapabilities(policyMap.get(name));
    if (caps.delegate) {
      violations.push({
        code: "target-delegates",
        reason: "target agent " + JSON.stringify(name) + " may delegate: delegation is depth 1",
      });
    }
    for (const capability of ["write", "execute", "delegate"]) {
      if (needs[capability] === true && caps[capability] !== true) {
        violations.push({
          code: "missing-capability",
          reason: "target agent " + JSON.stringify(name) + " lacks capability " + JSON.stringify(capability),
        });
      }
    }
  }

  const model = modelMap.get(name);
  if (typeof model !== "string" || model.length === 0) {
    violations.push({ code: "missing-model", reason: "no configured model for target agent " + JSON.stringify(name) });
  }

  return {
    ok: violations.length === 0,
    violations,
    model: typeof model === "string" && model.length > 0 ? model : undefined,
  };
}

/** Prefix every T3 Code session variable carries (e.g. `T3CODE_CLI_PATH`). */
const T3CODE_ENV_PREFIX = "T3CODE_";

/**
 * @deprecated Retained only for back-compat; superseded by `shouldNeuterExecute`,
 * which derives the execute-neuter exemption from the agent's declared policy.
 * The plugin no longer reads this list.
 *
 * Historically: agents exempt from execute-tool neutering, and ONLY while
 * running under the T3 Code harness. `build` is Monarch, the primary T3 Code
 * orchestrator: Code Mode is its only route to the browser tools, the MCP
 * servers and the T3 Code app tools, so neutering it removes those catalogs
 * outright. The exemption was safe because the harness sandbox was probed on
 * 2026-10-17 and cannot reach the filesystem or spawn a process
 * (`require`/`process` undefined, dynamic `import` unsupported, `fetch` rejects
 * `file:` URLs, an `execSync` write created no marker file) — there is nothing
 * to gate there.
 */
const EXECUTE_NEUTER_EXEMPT_AGENTS = ["build"];

/**
 * True when `env` looks like a T3 Code session environment. Detection is
 * prefix-based on an own enumerable key with a non-empty string value, so it
 * survives the specific `T3CODE_*` variable being renamed. Native OpenCode CLI
 * and service processes carry no such key.
 */
export function isT3CodeHarness(env) {
  if (!env || typeof env !== "object") return false;
  for (const key of Object.keys(env)) {
    if (!key.startsWith(T3CODE_ENV_PREFIX)) continue;
    const value = env[key];
    if (typeof value === "string" && value.length > 0) return true;
  }
  return false;
}

/**
 * True only for an exempt agent running under the T3 Code harness; false for
 * every other agent, for a non-string agent, and for a missing/non-object env.
 *
 * @deprecated Superseded by `shouldNeuterExecute`, which derives the exemption
 * from the agent's declared policy instead of a literal name list. Kept
 * exported for back-compat; the plugin no longer calls it.
 */
export function isExecuteNeuterExempt(agent, env) {
  return isT3CodeHarness(env) && EXECUTE_NEUTER_EXEMPT_AGENTS.includes(agent);
}

/** Build the agent -> rules map from a parsed opencode config object. */
export function buildPolicy(config) {
  const policy = new Map();
  const agents =
    config && typeof config === "object" && !Array.isArray(config) ? config.agents : undefined;
  if (!agents || typeof agents !== "object" || Array.isArray(agents)) return policy;

  for (const [name, def] of Object.entries(agents)) {
    if (!def || typeof def !== "object" || Array.isArray(def)) continue;
    if (!Array.isArray(def.permissions)) continue;
    policy.set(name, def.permissions.filter(isRule));
  }
  return policy;
}

// Parsed policies keyed by path, invalidated on mtime/size change (hot reload).
const policyCache = new Map();

/** Drop cached parses; used by tests that rewrite a fixture in place. */
export function resetPolicyCache() {
  policyCache.clear();
}

/**
 * Load `agents.<name>.permissions` from an opencode config file.
 *
 * A missing file, unreadable file, or malformed JSON yields an empty map
 * (fail-safe: the session keeps whatever rules it already had). The cheap
 * `statSync` per call picks up edits without re-parsing on every permission
 * check.
 */
export function loadPolicy(configPath) {
  if (typeof configPath !== "string" || configPath.length === 0) return new Map();

  let stamp;
  try {
    const st = statSync(configPath);
    stamp = `${st.mtimeMs}:${st.size}`;
  } catch {
    policyCache.delete(configPath);
    return new Map();
  }

  const cached = policyCache.get(configPath);
  if (cached !== undefined && cached.stamp === stamp) return cached.policy;

  let policy;
  try {
    policy = buildPolicy(JSON.parse(readFileSync(configPath, "utf8")));
  } catch {
    policy = new Map();
  }
  policyCache.set(configPath, { stamp, policy });
  return policy;
}

/** Configured agent models read from opencode config. */
export function getConfiguredAgentModels(config) {
  if (!config || typeof config !== "object" || !config.agents) return new Map();
  const agentModels = new Map();
  for (const [name, def] of Object.entries(config.agents)) {
    if (!def || typeof def !== "object") continue;
    if (def.model != null && typeof def.model === "string") agentModels.set(name, def.model);
  }
  return agentModels;
}

// Parsed agent->model maps keyed by path, invalidated on mtime/size change.
const agentModelCache = new Map();

/** Drop cached agent-model parses; used by tests that rewrite a fixture. */
export function resetAgentModelCache() {
  agentModelCache.clear();
}

/**
 * Load `agents.<name>.model` from an opencode config file. Same fail-safe and
 * hot-reload semantics as loadPolicy: a missing/unreadable/malformed file
 * yields an empty map rather than throwing.
 */
export function loadAgentModels(configPath) {
  if (typeof configPath !== "string" || configPath.length === 0) return new Map();

  let stamp;
  try {
    const st = statSync(configPath);
    stamp = `${st.mtimeMs}:${st.size}`;
  } catch {
    agentModelCache.delete(configPath);
    return new Map();
  }

  const cached = agentModelCache.get(configPath);
  if (cached !== undefined && cached.stamp === stamp) return cached.models;

  let models;
  try {
    models = getConfiguredAgentModels(JSON.parse(readFileSync(configPath, "utf8")));
  } catch {
    models = new Map();
  }
  agentModelCache.set(configPath, { stamp, models });
  return models;
}

/** Validate a native OpenCode subagent model against the declared routing class.
 *  Native OpenCode subagent input shape: {agent, model}.
 *
 *  This is a thin adapter over `resolveModelRouting`, which is the single
 *  authority for model classes; the expected model is the routed model, so the
 *  allowlist cannot drift from MODEL_ROUTING or opencode.base.json.
 *
 *  `valid` is true when no correction is needed, and also when the agent is
 *  unknown to routing (`known:false`) — there is no policy knowledge to judge
 *  it by. `violation:true` is set exactly when the caller must apply `expected`.
 *  A missing agent name stays a fail-closed violation. */
export function validateSubagentModel(agentName, inputModel, configuredModels) {
  if (!agentName) return { valid: false, reason: "missing agent name", violation: true };
  const routing = resolveModelRouting(agentName, inputModel, configuredModels);
  const base = { expected: routing.model, actual: inputModel, reason: routing.reason };
  if (!routing.known || !routing.corrected) return { valid: true, ...base };
  return { valid: false, ...base, violation: true };
}

/** Validate T3 delegate_task model/provider input.
 *  Reads actual T3 API shape: {task, target:{providerInstanceId, model}, role}.
 *  T3 `role` is one of research|implementation|review|design|test|general.
 *  The input carries NO `agent` field; only `target.providerInstanceId` and
 *  `target.model` identify the worker model.
 *  Requires explicit target.providerInstanceId and target.model.
 *
 *  SHAPE VALIDATOR ONLY — NOT AN ENFORCEMENT POINT. T3's `delegate_task` is an
 *  app-owned tool (it does not appear in the opencode binary) and does not fire
 *  the OpenCode `tool.hook("execute.before")` event this plugin listens on, so
 *  this helper cannot block a real dispatch and cannot map a role to a
 *  configured model. It exists so callers/tests can assert the correct target
 *  shape; the caller-side requirement is prompt-only (AGENTS.md + the
 *  build/monarch system text). */
export function validateDelegateTaskModel(input, configuredModels) {
  if (!input || typeof input !== "object") return { valid: false, reason: "missing delegate_task input", violation: true };
  // T3 API shape: {task, target:{providerInstanceId, model}, role}; no `agent`.
  const target = input.target;
  const { providerInstanceId, model } = target && typeof target === "object" ? target : {};
  // Fail closed: require explicit providerInstanceId and model
  if (!providerInstanceId) return { valid: false, reason: "missing target.providerInstanceId", violation: true };
  if (!model) return { valid: false, reason: "missing target.model", violation: true };
  // The T3 target carries no agent id to map back to a configured pin, so this
  // validator cannot compare against agents.<name>.model. It only proves the
  // caller supplied an explicit provider/model pair.
  return { valid: true, reason: "delegate_task has explicit providerInstanceId and model", providerInstanceId, model };
}

/** Check whether an agent has a declared model routing class, i.e. a dispatch to
 *  it is subject to `resolveModelRouting`. Pinned, explicit and escalation
 *  agents all route; unknown agents and non-workers (build, plan, ...) do not. */
export function isModelGatedSubagent(agentName) {
  return typeof agentName === "string" && Object.hasOwn(MODEL_ROUTING, agentName);
}

/* Model routing classes.
 * `pinned` workers always run the configured model. `explicit` workers may be
 * given any non-empty model by the caller. `escalation` workers are paid, so
 * only the listed paid models are honored; anything else falls back to the
 * configured model. Routing answers "which model", never "may this run" — a
 * paid escalation agent is never blocked on model grounds. */
export const MODEL_ROUTING = {
  bishop: { class: "pinned" },
  knight: { class: "pinned" },
  mercenary: { class: "explicit" },
  witch: { class: "escalation", allowed: ["opencode-go/deepseek-v4-pro", "opencode-go/gpt-5.6-luna"] },
  mage: { class: "escalation", allowed: ["opencode-go/glm-5.3"] },
  "grand-bishop": { class: "escalation", allowed: ["opencode-go/muse-spark-1.3-contributor"] },
  "knight-commander": { class: "escalation", allowed: ["opencode-go/muse-spark-1.3-contributor"] },
};

/**
 * Resolve the model a dispatch to `agentName` should run under. `known:false`
 * means the agent has neither a routing class nor a configured model, so the
 * caller leaves the input alone. `corrected` means the caller must apply
 * `model`; `reason` names the rejected value when one was corrected.
 */
export function resolveModelRouting(agentName, inputModel, configuredModels) {
  try {
    if (typeof agentName !== "string" || agentName.length === 0) {
      return { known: false, model: undefined, corrected: false, reason: "missing agent name" };
    }
    const route = Object.hasOwn(MODEL_ROUTING, agentName) ? MODEL_ROUTING[agentName] : undefined;
    const configured = configuredModels instanceof Map ? configuredModels.get(agentName) : undefined;
    const explicit = typeof inputModel === "string" && inputModel.length > 0;

    if (route === undefined) {
      if (typeof configured !== "string" || configured.length === 0) {
        return {
          known: false,
          model: undefined,
          corrected: false,
          reason: "no routing class or configured model for " + JSON.stringify(agentName),
        };
      }
      if (explicit) return { known: true, model: inputModel, corrected: false, reason: "explicit model honored" };
      return { known: true, model: configured, corrected: true, reason: "explicit model required; defaulted to configured" };
    }

    if (route.class === "pinned") {
      // Pinned: the configured model always wins; a different input is corrected.
      if (typeof configured === "string" && configured.length > 0) {
        return {
          known: true,
          model: configured,
          corrected: inputModel !== configured,
          reason: inputModel === configured ? "model matches the configured pin" : "pinned to " + JSON.stringify(configured),
        };
      }
      return {
        known: false,
        model: undefined,
        corrected: false,
        reason: "no configured model to pin for " + JSON.stringify(agentName),
      };
    }

    if (route.class === "explicit") {
      if (explicit) return { known: true, model: inputModel, corrected: false, reason: "explicit model honored" };
      return { known: true, model: configured, corrected: true, reason: "explicit model required; defaulted to configured" };
    }

    // Escalation: only the declared paid models are honored.
    if (explicit && route.allowed.includes(inputModel)) {
      return { known: true, model: inputModel, corrected: false, reason: "allowed escalation model honored" };
    }
    const model = configured ?? route.allowed[0];
    return {
      known: true,
      model,
      corrected: true,
      reason:
        "model " + JSON.stringify(inputModel ?? null) + " is not an allowed " + agentName +
        " escalation model; using " + JSON.stringify(model),
    };
  } catch {
    return { known: false, model: undefined, corrected: false, reason: "malformed routing input" };
  }
}

/* Delegation depth guard.
 * Observed 2026-10-21: a native-subagent worker called T3 delegate_task instead
 * of doing its task. delegate_task carries no agent id, so the child ran as
 * build (no edit, no shell), could do nothing, delegated again, and the chain
 * produced no output. Rule: delegation is depth 1 and only a root orchestrator
 * delegates. The native subagent tool is hook-visible and neutered here; T3
 * app-owned tools are not hook-visible, so for those this module only
 * classifies and AGENTS.md plus the worker prompt enforce. */

export const DELEGATION_TOOLS = new Set([
  "subagent",
  "functions.subagent",
  "delegate_task",
  "t3_thread_launch",
  "create_threads",
]);

/* Delegation floor.
 * Used only when the loaded policy cannot classify the identity: no policy, an
 * empty policy, or an agent the policy does not declare. It must list every
 * primary orchestrator declared in opencode.base.json (today `build` and
 * `monarch`); an orchestrator missing here loses delegation whenever its config
 * cannot be read. */
export const ROOT_ORCHESTRATOR_AGENTS = new Set(["build", "monarch"]);

export function isDelegationTool(toolName) {
  return typeof toolName === "string" && DELEGATION_TOOLS.has(toolName);
}

export function mayDelegate(agentName) {
  return typeof agentName === "string" && ROOT_ORCHESTRATOR_AGENTS.has(agentName);
}

export const BLOCKED_DELEGATION_AGENT = "__hierarchy-guard_delegation_blocked__";

/* Delegation is the one capability that fails closed.
 * Recursion is what cost three generations and a 90-minute hang on 2026-10-21,
 * and it is invisible until the session stalls; a false deny costs one line in
 * a session and leaves the loop running. So when the policy cannot speak for an
 * identity — no policy, empty policy, or an undeclared agent — only `build` and
 * `monarch` delegate and every other identity is blocked. Every other capability
 * still fails open. This is a deliberate exception to the general fail-safe
 * rule, recorded in docs/hierarchy-model.md.
 *
 * Two modes, same returned shape:
 *  - policy-derived: the identity is declared, so its derived `delegate` decides.
 *  - floor: the identity is not declared, so ROOT_ORCHESTRATOR_AGENTS decides.
 */
export function resolveNestedDelegation(agentName, toolName, input, policy) {
  if (!isDelegationTool(toolName)) return null;
  if (policy instanceof Map && policy.has(agentName)) {
    if (deriveCapabilities(policy.get(agentName)).delegate) return null;
  } else if (mayDelegate(agentName)) {
    return null;
  }
  const blocked = {
    blocked: true,
    agent: typeof agentName === "string" ? agentName : "",
    tool: toolName,
    reason: "agent " + JSON.stringify(agentName ?? "") + " may not delegate (" + toolName + "): delegation is depth 1, workers do the work themselves",
  };
  if (toolName === "subagent" && input && typeof input === "object") {
    blocked.input = { ...input, agent: BLOCKED_DELEGATION_AGENT, prompt: blocked.reason };
  }
  return blocked;
}

/**
 * Correct or block a native OpenCode `subagent` dispatch.
 *
 * The native subagent tool resolves its child model as
 * `input.model ?? agent.model ?? parentSession.model` (opencode v2.0.29
 * source), so an omitted or wrong `model` lets a worker inherit the parent's
 * model — the exact bug the pin guards. The `tool.hook("execute.before")`
 * handler mutates this input object in place (the subagent tool's own internal
 * hook relies on the same mutation, so the mechanism is proven).
 *
 * `policy` maps agent -> rules and, when non-empty, is also the dispatch
 * authority: a target absent from it is unknown (the runtime would resolve an
 * unidentified child), and a target whose derived capabilities include
 * `delegate` is itself an orchestrator (depth would exceed 1). An empty or
 * absent policy means nothing is declared, so no extra enforcement.
 *
 * Returns `null` when no change is needed. Otherwise `changed: true` with
 * `kind: "pin"` ({ agent, expected, actual, reason, input }, model pinned to
 * the routed model) or `kind: "blocked"` ({ agent, reason, input }, input.agent
 * replaced with BLOCKED_DELEGATION_AGENT and input.prompt with the reason).
 * `resolveModelRouting` falls back to the hardcoded bishop/knight pins when the
 * config declares none, so a pinned child never inherits the parent's model.
 */
export function resolveSubagentInput(input, configuredModels, policy) {
  if (!input || typeof input !== "object") return null;
  const agentName = typeof input.agent === "string" ? input.agent : "";
  if (agentName.length === 0) return null;

  if (policy instanceof Map && policy.size > 0) {
    let reason = null;
    if (!policy.has(agentName)) {
      reason = "unknown agent " + JSON.stringify(agentName) + ": not declared in policy";
    } else if (deriveCapabilities(policy.get(agentName)).delegate) {
      reason = "agent " + JSON.stringify(agentName) + " may delegate: delegation is depth 1";
    }
    if (reason !== null) {
      return {
        changed: true,
        kind: "blocked",
        agent: agentName,
        reason,
        input: { ...input, agent: BLOCKED_DELEGATION_AGENT, prompt: reason },
      };
    }
  }

  const routing = resolveModelRouting(agentName, input.model, configuredModels);
  if (!routing.known || !routing.corrected) return null;
  return {
    changed: true,
    kind: "pin",
    agent: agentName,
    expected: routing.model,
    actual: input.model,
    reason: routing.reason,
    input: { ...input, model: routing.model },
  };
}
