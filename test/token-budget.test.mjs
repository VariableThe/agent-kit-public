import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// Budgets are for the injected context: AGENTS.md rides every session, and every
// skill description rides the skill catalog. They exist to stop slow growth, not
// to force churn — raise a cap deliberately, in the same commit that needs it.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");

const AGENTS_MD_MAX_CHARS = 5200;
const SKILL_DESCRIPTIONS_MAX_CHARS = 6000;

function stripQuotes(value) {
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

describe("token budget", () => {
  it("keeps AGENTS.md within its injected-context budget", () => {
    const text = readFileSync(join(root, "AGENTS.md"), "utf8");
    assert.ok(
      text.length <= AGENTS_MD_MAX_CHARS,
      `AGENTS.md is ${text.length} chars, over the ${AGENTS_MD_MAX_CHARS} budget`,
    );
  });

  it("keeps the skill catalog descriptions within budget", () => {
    const skillsDir = join(root, "skills");
    let total = 0;
    const offenders = [];
    for (const entry of readdirSync(skillsDir)) {
      const skillPath = join(skillsDir, entry, "SKILL.md");
      let st;
      try {
        st = statSync(skillPath);
      } catch {
        continue;
      }
      if (!st.isFile()) continue;
      const content = readFileSync(skillPath, "utf8");
      const match = content.match(/^description:\s*(.+)$/m);
      if (!match) {
        offenders.push(`${entry}: no description`);
        continue;
      }
      total += stripQuotes(match[1].trim()).length;
    }
    assert.deepEqual(offenders, []);
    assert.ok(
      total <= SKILL_DESCRIPTIONS_MAX_CHARS,
      `skill descriptions total ${total} chars, over the ${SKILL_DESCRIPTIONS_MAX_CHARS} budget`,
    );
  });

  it("gives every skill a name and a description", () => {
    const skillsDir = join(root, "skills");
    for (const entry of readdirSync(skillsDir)) {
      const skillPath = join(skillsDir, entry, "SKILL.md");
      let st;
      try {
        st = statSync(skillPath);
      } catch {
        continue;
      }
      if (!st.isFile()) continue;
      const content = readFileSync(skillPath, "utf8");
      assert.ok(/^name:\s*\S/m.test(content), `${entry}: missing frontmatter name`);
      assert.ok(/^description:\s*\S/m.test(content), `${entry}: missing frontmatter description`);
    }
  });
});
