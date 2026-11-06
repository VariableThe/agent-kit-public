import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  BLOCKED_DELEGATION_AGENT,
  deriveCapabilities,
  isDelegationTool,
  isModelGatedSubagent,
  matchesPattern,
  mayDelegate,
  resolveEffect,
  resolveExecutor,
  resolveModelRouting,
  resolveNestedDelegation,
  resolveSubagentInput,
  shouldNeuterExecute,
} from "../plugins/hierarchy-guard/policy.mjs";

const allow = (action, resource) => ({ action, resource, effect: "allow" });
const deny = (action, resource) => ({ action, resource, effect: "deny" });

describe("policy matcher", () => {
  it("anchors globs and makes a trailing argument group optional", () => {
    assert.equal(matchesPattern("bash", "bash *"), true);
    assert.equal(matchesPattern("bash -c ls", "bash *"), true);
    assert.equal(matchesPattern("bashful", "bash *"), false);
    assert.equal(matchesPattern("edit", "bash *"), false);
  });

  it("normalizes backslashes to forward slashes on both sides", () => {
    assert.equal(matchesPattern("C:\\tmp\\a.txt", "C:/tmp/*"), true);
    assert.equal(matchesPattern("C:/tmp/a.txt", "C:\\tmp\\*"), true);
  });

  it("treats a non-string pattern as never matching", () => {
    assert.equal(matchesPattern("edit", undefined), false);
  });
});

describe("policy effect resolution", () => {
  it("uses last-match-wins", () => {
    const rules = [allow("edit", "*"), deny("edit", "*.env")];
    assert.equal(resolveEffect("edit", ["*.env"], rules).effect, "deny");
    assert.equal(resolveEffect("edit", ["notes.md"], rules).effect, "allow");
  });

  it("denies when any resource denies, even if another allows", () => {
    const rules = [allow("edit", "*"), deny("edit", "*.pem")];
    assert.equal(resolveEffect("edit", ["notes.md", "key.pem"], rules).effect, "deny");
  });

  it("defaults an unknown or missing effect to ask", () => {
    assert.equal(resolveEffect("edit", ["x"], [{ action: "edit", resource: "*" }]).effect, "ask");
    assert.equal(
      resolveEffect("edit", ["x"], [{ action: "edit", resource: "*", effect: "nonsense" }]).effect,
      "ask",
    );
  });

  it("defaults to ask when nothing matches", () => {
    assert.equal(resolveEffect("shell", ["ls"], [allow("edit", "*")]).effect, "ask");
  });
});

describe("capability derivation", () => {
  it("reports unknown for a non-array rule list", () => {
    assert.deepEqual(deriveCapabilities(undefined), {
      known: false,
      write: false,
      execute: false,
      targets: [],
      delegate: false,
    });
  });

  it("derives write and execute from what the policy denies", () => {
    const caps = deriveCapabilities([deny("edit", "*"), deny("shell", "*")]);
    assert.equal(caps.known, true);
    assert.equal(caps.write, false);
    assert.equal(caps.execute, false);
    assert.equal(caps.delegate, false);
  });

  it("only counts a dispatch target the policy really allows", () => {
    const caps = deriveCapabilities([allow("subagent", "knight"), deny("subagent", "monarch")]);
    assert.deepEqual(caps.targets, ["knight"]);
    assert.equal(caps.delegate, true);
  });

  it("does not treat a lone subagent deny as delegation capability", () => {
    assert.equal(deriveCapabilities([deny("subagent", "*")]).delegate, false);
  });
});

describe("execute gating", () => {
  it("detects the T3 Code harness by environment prefix", () => {
    assert.equal(resolveExecutor({}), "host");
    assert.equal(resolveExecutor({ T3CODE_CLI_PATH: "/usr/local/bin/t3" }), "restricted");
    assert.equal(resolveExecutor({ T3CODE_CLI_PATH: "" }), "host");
    assert.equal(resolveExecutor({ PATH: "/usr/bin" }), "host");
  });

  it("neuters an agent that cannot execute and has no other route", () => {
    assert.equal(shouldNeuterExecute({ execute: false, delegate: false }, "host"), true);
    assert.equal(shouldNeuterExecute({ execute: false, delegate: false }, "restricted"), true);
    assert.equal(shouldNeuterExecute({ execute: false, delegate: true }, "host"), true);
    assert.equal(shouldNeuterExecute({ execute: false, delegate: true }, "restricted"), false);
    assert.equal(shouldNeuterExecute({ execute: true, delegate: false }, "host"), false);
  });
});

describe("model routing", () => {
  const configured = new Map([
    ["bishop", "provider/model-pinned"],
    ["knight", "provider/model-pinned"],
    ["mercenary", "provider/model-default"],
    ["mage", "provider/model-escalation"],
  ]);

  it("pins a pinned agent to its configured model", () => {
    const result = resolveModelRouting("bishop", "provider/other", configured);
    assert.equal(result.known, true);
    assert.equal(result.corrected, true);
    assert.equal(result.model, "provider/model-pinned");
  });

  it("leaves a pinned agent alone when the input already matches", () => {
    const result = resolveModelRouting("knight", "provider/model-pinned", configured);
    assert.equal(result.corrected, false);
    assert.equal(result.model, "provider/model-pinned");
  });

  it("honors an explicit model for an explicit agent", () => {
    const result = resolveModelRouting("mercenary", "provider/any", configured);
    assert.equal(result.corrected, false);
    assert.equal(result.model, "provider/any");
  });

  it("defaults an explicit agent with no input to its configured model", () => {
    const result = resolveModelRouting("mercenary", undefined, configured);
    assert.equal(result.corrected, true);
    assert.equal(result.model, "provider/model-default");
  });

  it("rejects an undeclared model for an escalation agent", () => {
    const result = resolveModelRouting("mage", "provider/free-model", configured);
    assert.equal(result.corrected, true);
    assert.equal(result.model, "provider/model-escalation");
  });

  it("reports unknown for an agent with neither a class nor a configured model", () => {
    assert.equal(resolveModelRouting("stranger", undefined, configured).known, false);
  });

  it("gates only agents that carry a routing class", () => {
    assert.equal(isModelGatedSubagent("bishop"), true);
    assert.equal(isModelGatedSubagent("build"), false);
    assert.equal(isModelGatedSubagent(undefined), false);
  });
});

describe("delegation depth", () => {
  const workerRules = [deny("shell", "*"), deny("edit", "*")];
  const rootRules = [allow("subagent", "knight")];

  it("recognizes the delegation tools", () => {
    assert.equal(isDelegationTool("subagent"), true);
    assert.equal(isDelegationTool("delegate_task"), true);
    assert.equal(isDelegationTool("bash"), false);
  });

  it("blocks a declared worker from delegating", () => {
    const policy = new Map([["knight", workerRules]]);
    const result = resolveNestedDelegation("knight", "subagent", { agent: "mercenary" }, policy);
    assert.equal(result.blocked, true);
    assert.equal(result.input.agent, BLOCKED_DELEGATION_AGENT);
  });

  it("lets a declared orchestrator delegate", () => {
    const policy = new Map([["build", rootRules]]);
    assert.equal(resolveNestedDelegation("build", "subagent", { agent: "knight" }, policy), null);
  });

  it("fails closed for an identity the policy cannot classify", () => {
    const policy = new Map([["knight", workerRules]]);
    assert.equal(resolveNestedDelegation("stranger", "subagent", {}, policy).blocked, true);
    // Empty policy: only the delegation floor may delegate.
    assert.equal(resolveNestedDelegation("build", "subagent", {}, new Map()), null);
    assert.equal(resolveNestedDelegation("knight", "subagent", {}, new Map()).blocked, true);
    assert.equal(mayDelegate("build"), true);
    assert.equal(mayDelegate("stranger"), false);
  });

  it("ignores non-delegation tools", () => {
    const policy = new Map([["knight", workerRules]]);
    assert.equal(resolveNestedDelegation("knight", "bash", {}, policy), null);
  });
});

describe("subagent input resolution", () => {
  const configured = new Map([["knight", "provider/model-pinned"]]);

  it("blocks a dispatch to an agent the policy does not declare", () => {
    const policy = new Map([["knight", [allow("edit", "*")]]]);
    const result = resolveSubagentInput({ agent: "ghost" }, configured, policy);
    assert.equal(result.kind, "blocked");
    assert.match(result.reason, /not declared in policy/);
  });

  it("blocks a dispatch to another orchestrator", () => {
    const policy = new Map([
      ["knight", [allow("edit", "*")]],
      ["build", [allow("subagent", "knight")]],
    ]);
    const result = resolveSubagentInput({ agent: "build" }, configured, policy);
    assert.equal(result.kind, "blocked");
    assert.match(result.reason, /delegation is depth 1/);
  });

  it("pins the model of a routed worker", () => {
    const policy = new Map([["knight", [allow("edit", "*")]]]);
    const result = resolveSubagentInput({ agent: "knight", model: "provider/other" }, configured, policy);
    assert.equal(result.kind, "pin");
    assert.equal(result.input.model, "provider/model-pinned");
  });

  it("returns null when no correction is needed", () => {
    const policy = new Map([["knight", [allow("edit", "*")]]]);
    assert.equal(
      resolveSubagentInput({ agent: "knight", model: "provider/model-pinned" }, configured, policy),
      null,
    );
  });

  it("ignores an empty agent name", () => {
    assert.equal(resolveSubagentInput({}, configured, new Map()), null);
  });
});
