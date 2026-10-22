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
 * `pinned` workers always run the configured model, and the config pins them to
 * free models, so a mistaken input cannot spend money. `explicit` workers may be
 * given any non-empty model by the caller. `escalation` workers are paid, so
 * only the listed paid models are honored; anything else falls back to the
 * configured model. Routing answers "which model", never "may this run" — a
 * paid escalation agent is never blocked on model grounds. */
export const MODEL_ROUTING = {
  bishop: { class: "pinned" },
  knight: { class: "pinned" },
  "p8-worker": { class: "pinned" },
  mercenary: { class: "explicit" },
  witch: { class: "escalation", allowed: ["opencode-go/deepseek-v4-pro", "opencode-go/gpt-5.6-luna"] },
  mage: { class: "escalation", allowed: ["opencode-go/glm-5.3"] },
  "grand-bishop": { class: "escalation", allowed: ["opencode-go/muse-spark-1.3-contributor"] },
  "knight-commander": { class: "escalation", allowed: ["opencode-go/muse-spark-1.3-contributor"] },
};

/** Pins used when the config declares no model; config always wins when present. */
const PINNED_MODEL_FALLBACKS = {
  bishop: "p8-agent/deepseek-v4-pro",
  knight: "p8-agent/deepseek-v4-pro",
  "p8-worker": "p8-agent/gemma4:31b",
};

export const P8_PINNED_LADDER = ["p8-agent/deepseek-v4-pro", "p8-agent/gemma4:31b", "p8-agent/nemotron-3-ultra", "p8-agent/kimi-k3:cloud", "p8-agent/router/agent"];

const P8_PINNED_LADDERS = new Map([
  ["bishop", [...P8_PINNED_LADDER]],
  ["knight", [...P8_PINNED_LADDER]],
  ["p8-worker", [...P8_PINNED_LADDER]],
]);

export function getPinLadder(agent) {
  if (typeof agent === "string" && Array.isArray(P8_PINNED_LADDERS[agent])) {
    return [...P8_PINNED_LADDERS[agent]];
  }
  return [...P8_PINNED_LADDER];
}

export function p8LadderFor(agent) {
  return getPinLadder(agent);
}

/**
 * Resolve the model a dispatch to `agentName` should run under. `known:false`
 * means the agent has neither a routing class nor a configured model, so the
 * caller leaves the input alone. `corrected` means the caller must apply
 * `model`; `reason` names the rejected value when one was corrected.
 */
export function resolveModelRouting(agentName, inputModel, configuredModels, options) {
  try {
    if (typeof agentName !== "string" || agentName.length === 0) {
      return { known: false, model: undefined, corrected: false, reason: "missing agent name" };
    }
    const route = Object.hasOwn(MODEL_ROUTING, agentName) ? MODEL_ROUTING[agentName] : undefined;
    const configured = configuredModels instanceof Map ? configuredModels.get(agentName) : undefined;
    const explicit = typeof inputModel === "string" && inputModel.length > 0;

    if (route === undefined) {
      // No declared class: only a configured agent is routeable, and its
      // configured model is the safest thing to run. An unknown agent is left
      // to the caller (fail-safe: no policy knowledge, no correction).
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
      const ladder = getPinLadder(agentName);
      const configuredStr = typeof configured === "string" && configured.length > 0 ? configured : undefined;
      const firstCandidate = configuredStr ?? (Array.isArray(ladder) && ladder.length ? ladder[0] : undefined) ?? PINNED_MODEL_FALLBACKS[agentName];
      const candidates = [];
      if (configuredStr) candidates.push(configuredStr);
      if (Array.isArray(ladder)) for (const m of ladder) candidates.push(m);
      if (PINNED_MODEL_FALLBACKS[agentName]) candidates.push(PINNED_MODEL_FALLBACKS[agentName]);
      const unique = [...new Set(candidates.filter((m) => typeof m === "string" && m.length > 0 && isP8Model(m)))];
      // All candidates must be p8 models; paid models are never run by pinned workers.
      const isAvailable = (model) => toBooleanStatus(resolveP8Availability(model, options));
      let chosen;
      for (const c of unique) {
        if (isAvailable(c)) { chosen = c; break; }
      }
      if (chosen === undefined) {
        return {
          known: true,
          model: undefined,
          corrected: false,
          reason: "no available p8 model for " + JSON.stringify(agentName),
        };
      }
      return {
        known: true,
        model: chosen,
        corrected: inputModel !== chosen,
        reason: inputModel === chosen ? "model matches the configured pin" : "pinned to " + JSON.stringify(chosen),
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
export function resolveSubagentInput(input, configuredModels, policy, options) {
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

  const routing = resolveModelRouting(agentName, input.model, configuredModels, options);
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


/* P8 model availability cache and pinned ladder helpers. */

const p8ModelAvailabilityCache = new Map();
export { p8ModelAvailabilityCache };
export const P8_MODEL_AVAILABILITY = p8ModelAvailabilityCache;
export const p8AvailabilityCache = p8ModelAvailabilityCache;
export const p8ModelAvailability = p8ModelAvailabilityCache;
export const p8Availability = p8ModelAvailabilityCache;

function isP8Model(model) {
  return typeof model === "string" && (model.startsWith("p8-agent/") || model.startsWith("p8-agent-fast/"));
}

export function resetP8ModelAvailabilityCache() {
  p8ModelAvailabilityCache.clear();
}

function toBooleanStatus(value) {
  if (value === undefined || value === null) return true;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value >= 200 && value < 300;
  if (typeof value === "string") {
    const lower = value.trim().toLowerCase();
    if (lower === "true" || lower === "available" || lower === "ok" || lower === "success") return true;
    if (lower === "false" || lower === "unavailable" || lower === "down") return false;
    return true;
  }
  if (value && typeof value === "object") {
    if (value instanceof Map) return true;
    if (Object.prototype.hasOwnProperty.call(value, "available")) return !!value.available;
    if (Object.prototype.hasOwnProperty.call(value, "ok")) return !!value.ok;
    if (Object.prototype.hasOwnProperty.call(value, "success")) return !!value.success;
    if (Object.prototype.hasOwnProperty.call(value, "status")) {
      const s = value.status;
      if (typeof s === "number") return s >= 200 && s < 300;
      if (typeof s === "string") {
        const lower = s.trim().toLowerCase();
        return lower.includes("ok") || lower === "200";
      }
    }
    return true;
  }
  return true;
}

function normalizeAvailableValue(value) {
  if (value === true) return { available: true, ok: true, status: 200 };
  if (value === false) return { available: false, ok: false, status: 503 };
  if (value === undefined || value === null) return { available: true, ok: true, status: 200 };
  if (typeof value === "number") {
    const available = value >= 200 && value < 300;
    return { available, ok: available, status: value };
  }
  if (typeof value === "string") {
    const available = toBooleanStatus(value);
    return { available, ok: available, status: available ? 200 : 503 };
  }
  if (value && typeof value === "object") {
    const available = toBooleanStatus(value);
    const status =
      typeof value.status === "number"
        ? value.status
        : available
        ? 200
        : 503;
    return { available, ok: available, status, retryAfter: value.retryAfter };
  }
  return { available: true, ok: true, status: 200 };
}

function parseRetryAfterValue(value) {
  if (value === undefined || value === null) return undefined;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return Math.max(0, numeric) * 1000;
  const parsedDate = Date.parse(String(value));
  if (Number.isFinite(parsedDate)) return Math.max(0, parsedDate - Date.now());
  return undefined;
}

function getCacheMap(cache) {
  if (cache instanceof Map) return cache;
  return p8ModelAvailabilityCache;
}

export function getP8ModelAvailability(model, options) {
  const opts = options && typeof options === "object" ? options : {};
  const now = Number.isFinite(opts.now) ? opts.now : Date.now();
  const cache = getCacheMap(opts.cache);
  if (!cache.has(model)) return undefined;
  const entry = cache.get(model);
  if (!entry || typeof entry !== "object") {
    cache.delete(model);
    return undefined;
  }
  if (typeof entry.expiresAt === "number" && entry.expiresAt <= now) {
    cache.delete(model);
    return undefined;
  }
  if (typeof entry.available === "boolean") return entry.available;
  // backward compatibility: status objects without available
  return toBooleanStatus(entry);
}

export function recordP8ModelAvailability(model, available, options) {
  if (typeof model !== "string" || model.length === 0) return undefined;
  const opts = options && typeof options === "object" ? options : {};
  const now = Number.isFinite(opts.now) ? opts.now : Date.now();
  const cache = getCacheMap(opts.cache);
  const normalized = normalizeAvailableValue(available);
  // Also allow options.available to supply status when called as record(model, options)
  const effectiveAvailable = (available && typeof available === "object" && !opts.available && Object.keys(opts).length === 0 && available.available === undefined && available.status === undefined)
    ? normalizeAvailableValue(available)
    : normalized;

  let retryAfterMs = opts.retryAfterMs;
  if (retryAfterMs === undefined && opts.retryAfter !== undefined) {
    retryAfterMs = parseRetryAfterValue(opts.retryAfter);
  }
  if (retryAfterMs === undefined && effectiveAvailable.retryAfter !== undefined) {
    retryAfterMs = parseRetryAfterValue(effectiveAvailable.retryAfter);
  }

  let ttlMs = opts.ttlMs;
  if (ttlMs === undefined && opts.ttl !== undefined) ttlMs = opts.ttl;
  if (ttlMs === undefined) {
    if (retryAfterMs !== undefined && available !== true && effectiveAvailable.available === false) ttlMs = retryAfterMs;
    else ttlMs = effectiveAvailable.available ? 15000 : 30000;
  }

  const entry = {
    available: effectiveAvailable.available,
    expiresAt: now + Math.max(0, Number.isFinite(ttlMs) ? ttlMs : 0),
    retryAfterMs,
  };
  cache.set(model, entry);
  return { ...entry, ok: effectiveAvailable.available, status: effectiveAvailable.status };
}

// backward compatible positional form: recordP8ModelAvailability(model, {status})
// already handled above because available can be object and options empty.

export async function probeP8ModelAvailability(model, options) {
  const opts = options && typeof options === "object" ? options : {};
  const fetchImpl =
    typeof opts.fetchImpl === "function"
      ? opts.fetchImpl
      : typeof opts.fetch === "function"
      ? opts.fetch
      : typeof globalThis.fetch === "function"
      ? globalThis.fetch
      : undefined;

  const baseUrl = opts.baseUrl || opts.baseURL || opts.apiBase || opts.base || "https://ollama.ecitis.org/agent/v1";
  const timeoutMs = Number.isFinite(opts.timeoutMs)
    ? opts.timeoutMs
    : Number.isFinite(opts.timeout)
    ? opts.timeout
    : 5000;

  const apiKey = opts.apiKey || opts.token || opts.authToken || opts.key;
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (apiKey && !headers.Authorization && !headers.authorization) {
    headers.Authorization = "Bearer " + apiKey;
  }

  let url = opts.url || opts.endpoint || opts.endpointUrl;
  if (!url) {
    const base = String(baseUrl).replace(/\/+$/, "");
    url = base.endsWith("/chat/completions") ? base : base + "/chat/completions";
  }

  const body =
    opts.body && typeof opts.body === "object"
      ? opts.body
      : { model, messages: [{ role: "user", content: "ping" }], max_tokens: 1 };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let result;
  try {
    if (typeof fetchImpl !== "function") throw new Error("fetch is not available");
    const response = await fetchImpl(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const retryAfterRaw =
      response && response.headers && typeof response.headers.get === "function"
        ? response.headers.get("retry-after") || response.headers.get("Retry-After")
        : undefined;
    const retryAfterMs = parseRetryAfterValue(retryAfterRaw);

    const status = typeof response.status === "number" ? response.status : (response && response.ok ? 200 : 503);
    const ok = !!(response && response.ok === true) || (status >= 200 && status < 300);

    // Use opts.ttlMs as default TTL when finite, otherwise fall back to hardcoded defaults
    const defaultAvailableTtlMs = Number.isFinite(opts.ttlMs) ? opts.ttlMs : 15000;
    const defaultUnavailableTtlMs = Number.isFinite(opts.ttlMs) ? opts.ttlMs : 30000;

    if (status >= 200 && status < 300) {
      recordP8ModelAvailability(model, true, { now: undefined, ttlMs: defaultAvailableTtlMs, cache: opts.cache });
    } else if (status === 429) {
      recordP8ModelAvailability(model, false, { now: undefined, retryAfterMs, ttlMs: retryAfterMs !== undefined ? retryAfterMs : defaultUnavailableTtlMs, cache: opts.cache });
    } else if (status >= 500 || status === 0) {
      recordP8ModelAvailability(model, false, { ttlMs: defaultUnavailableTtlMs, cache: opts.cache });
    } else {
      // Other statuses (4xx) treat as unavailable with default unavailable TTL
      recordP8ModelAvailability(model, false, { ttlMs: defaultUnavailableTtlMs, cache: opts.cache });
    }

    result = {
      model,
      available: status >= 200 && status < 300,
      ok: status >= 200 && status < 300,
      status,
      retryAfter: retryAfterRaw,
      retryAfterMs,
    };
  } catch (error) {
    recordP8ModelAvailability(model, false, { ttlMs: defaultUnavailableTtlMs, cache: opts.cache });
    result = {
      model,
      available: false,
      ok: false,
      status: 0,
      error: error && error.message ? error.message : String(error),
    };
  } finally {
    clearTimeout(timer);
  }
  return result;
}

// Availability interpretation helpers.
export function availabilityFromOptions(options) {
  // Support call forms:
  // availabilityFromOptions(options) -> (model) => boolean|undefined
  // availabilityFromOptions(model, options) -> boolean|undefined
  if (arguments.length >= 2) {
    const model = arguments[0];
    const opts = arguments[1];
    const available = resolveP8Availability(model, opts);
    if (available === true || available === false) return available;
    return toBooleanStatus(available);
  }
  const opts = options;
  return (model) => {
    const available = resolveP8Availability(model, opts);
    if (available === true || available === false) return available;
    return toBooleanStatus(available);
  };
}

function resolveP8Availability(model, options) {
  const now = Date.now();
  if (options == null) {
    const cached = getP8ModelAvailability(model, { now, cache: p8ModelAvailabilityCache });
    if (cached === undefined) return true;
    return cached;
  }
  if (typeof options === "function") {
    try {
      const value = options(model);
      return value;
    } catch {
      return false;
    }
  }
  if (Array.isArray(options)) {
    return options.includes(model);
  }
  if (options instanceof Map) {
    if (options.has(model)) return options.get(model);
    return true;
  }
  if (typeof options === "object") {
    // Candidate arrays under common keys.
    for (const key of ["availableModels", "availableList", "models"]) {
      if (Array.isArray(options[key])) return options[key].includes(model);
    }
    // Mapping sources under common keys.
    for (const key of ["availability", "modelAvailability", "probe", "isAvailable", "available"]) {
      const src = options[key];
      if (src === undefined) continue;
      if (typeof src === "function") return src(model);
      if (src instanceof Map) {
        if (src.has(model)) return src.get(model);
        return true;
      }
      if (Array.isArray(src)) return src.includes(model);
      if (src && typeof src === "object") {
        if (Object.prototype.hasOwnProperty.call(src, model)) return src[model];
        // global status object applies to all candidates
        if (
          Object.prototype.hasOwnProperty.call(src, "available") ||
          Object.prototype.hasOwnProperty.call(src, "ok") ||
          Object.prototype.hasOwnProperty.call(src, "success") ||
          Object.prototype.hasOwnProperty.call(src, "status")
        ) {
          return src;
        }
      }
      if (typeof src === "boolean") return src;
      if (typeof src === "number") return src;
    }
    // Direct mapping on the options object itself.
    if (Object.prototype.hasOwnProperty.call(options, model)) {
      return options[model];
    }
    // Global status object.
    if (
      Object.prototype.hasOwnProperty.call(options, "available") ||
      Object.prototype.hasOwnProperty.call(options, "ok") ||
      Object.prototype.hasOwnProperty.call(options, "success") ||
      Object.prototype.hasOwnProperty.call(options, "status")
    ) {
      return options;
    }
    if (Array.isArray(options.availableModels) ) return options.availableModels.includes(model);
  }
  // cache in options
  if (options && options.cache instanceof Map) {
    const c = getP8ModelAvailability(model, { now, cache: options.cache });
    if (c !== undefined) return c;
  }
  const cached = getP8ModelAvailability(model, { now, cache: p8ModelAvailabilityCache });
  if (cached !== undefined) return cached;
  return true;
}

// candidate filtering helper exported for tests
export function isP8Candidate(model) {
  return isP8Model(model);
}

// Aliases.
export const probeP8Availability = probeP8ModelAvailability;
export const probeP8Model = probeP8ModelAvailability;
export const resetP8AvailabilityCache = resetP8ModelAvailabilityCache;
export const resetP8Cache = resetP8ModelAvailabilityCache;
export const getP8Availability = getP8ModelAvailability;
export const setP8Availability = recordP8ModelAvailability;
export const setP8ModelAvailability = recordP8ModelAvailability;
export const recordP8Availability = recordP8ModelAvailability;
export const getPinLadderAlias = getPinLadder;
