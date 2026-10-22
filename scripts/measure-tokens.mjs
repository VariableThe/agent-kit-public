#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

function measure(text) {
  const chars = text.length;
  return { chars, tokens: chars / 4 };
}

function read(rel) {
  return readFileSync(join(root, rel), "utf8");
}

const agentsMd = read("AGENTS.md");

const config = JSON.parse(read("opencode.base.json"));
const agentSystems = {};
const systemStrings = [];
for (const [name, def] of Object.entries(config.agents || {})) {
  const sys = typeof def?.system === "string" ? def.system : "";
  agentSystems[name] = measure(sys);
  systemStrings.push(sys);
}
agentSystems.total = measure(systemStrings.join(""));

let frontmatterTotal = 0;
let descriptionsTotal = 0;
const skillsDir = join(root, "skills");
for (const entry of readdirSync(skillsDir)) {
  const skillPath = join(skillsDir, entry, "SKILL.md");
  let st;
  try { st = statSync(skillPath); } catch { continue; }
  if (!st.isFile()) continue;
  const content = readFileSync(skillPath, "utf8");
  const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const fm = m ? m[0] : "";
  frontmatterTotal += fm.length;
  const dm = content.match(/^description:\s*(.+)$/m);
  if (dm) {
    let val = dm[1].trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    descriptionsTotal += val.length;
  }
}

const afterTxt = read("docs/scheduled-daily-model-check/after.txt");

const result = {
  agentsMd: measure(agentsMd),
  agentSystems,
  skills: {
    frontmatter: { chars: frontmatterTotal, tokens: frontmatterTotal / 4 },
    descriptions: { chars: descriptionsTotal, tokens: descriptionsTotal / 4 },
  },
  scheduledDailyModelCheckAfter: measure(afterTxt),
};

process.stdout.write(JSON.stringify(result, null, 2) + "\n");
