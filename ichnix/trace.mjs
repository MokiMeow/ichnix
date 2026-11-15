import { readFileSync } from "node:fs";
import { render } from "pele";
import { withWorkspaceColors } from "../scripts/diagram-theme.mjs";

const plan = JSON.parse(readFileSync(0, "utf8"));
const statuses = ["failed", "inconclusive", "passed"];

function label(value, limit = 36) {
  // Only our templates introduce Mermaid syntax or markup.
  const characters = Array.from(
    String(value)
      .replace(/[^\p{L}\p{M}\p{N} ._:/-]/gu, " ")
      .replace(/\s+/g, " ")
      .trim(),
  );
  if (!characters.length) return "Unnamed";
  return characters.length > limit
    ? characters.slice(0, limit - 1).join("") + "…"
    : characters.join("");
}

function provenance(method) {
  if (method === "hmac-sha256") return "Signed manifest";
  if (method === "local-file") return "Local manifest";
  return "CI record";
}

function manifestMatrix(p) {
  const { method, prefix } = p.manifest;
  const lines = [
    "block-beta",
    "columns 2",
    `C["${label(p.case)} · ${label(p.revision)}<br/>Run ${label(p.run, 24)}"]:2`,
    `M["${provenance(method)}<br/>SHA-256 prefix · ${label(prefix, 12)}"]:2`,
  ];
  for (const status of statuses) {
    const { count, steps } = p.controls[status];
    if (!count) continue;
    const heading = status[0].toUpperCase() + status.slice(1);
    const names = steps.map((step) => label(step, 34));
    if (count > steps.length) names.push(`+${count - steps.length} more`);
    lines.push(
      `S${status}["${heading} · ${count}"] T${status}["${names.join("<br/>")}"]`,
    );
  }
  return lines;
}

function evidenceTrace(p) {
  if (!p.events.length)
    return [
      "flowchart TB",
      `C["${label(p.case)}<br/>${label(p.component)} at ${label(p.revision)}"]`,
      'C -.-> E["No staging observations"]',
    ];
  if (p.manifest) return manifestMatrix(p);
  const sources = new Set(p.events.map((e) => provenance(e.source_method)));
  const lines = [
    "sequenceDiagram",
    `accTitle: Observed evidence for ${label(p.case)}`,
    `participant C as ${sources.size === 1 ? [...sources][0] : "CI sources"}`,
    "participant G as Evidence graph",
    `Note over C,G: ${label(p.case)} / ${label(p.revision)}`,
    `Note over C,G: ${p.run ? "Run " + label(p.run) : "Run ID unrecorded"}`,
  ];
  let previous;
  for (const event of p.events) {
    const source = provenance(event.source_method);
    if (sources.size > 1 && source !== previous)
      lines.push(`Note over C: ${source}`);
    const arrow = event.result === "inconclusive" ? "-->>" : "->>";
    lines.push(`C${arrow}G: ${label(event.step)} (${label(event.result, 12)})`);
    previous = source;
  }
  if (p.omitted)
    lines.push(`Note over C,G: ${p.omitted} further observations in this run`);
  return lines;
}

function reviewDiagram(p) {
  const { passed, failed, inconclusive } = p.controls;
  const observed = passed.count + failed.count + inconclusive.count;
  const summary = observed
    ? `${passed.count} passed / ${failed.count} failed / ${inconclusive.count} inconclusive`
    : "Await staging evidence";
  const links = {
    "scope-first": ["C --> S", "S --> E", "S --> O"],
    "evidence-first": ["C --> E", "C --> S", "S --> O"],
    "owner-first": ["C --> O", "C --> S", "S --> E"],
  };
  const lines = [
    "flowchart TB",
    `accTitle: Review workflow for ${label(p.case)}`,
    `C["${label(p.case)}"]`,
    `S["${label(p.service)} / ${label(p.component)}<br/>${label(p.revision)}"]`,
    `accDescr: ${summary}. The owner recommendation and last case decision are shown separately.`,
    `E["${observed ? (p.run ? "Latest staging run" : "CI records without a run ID") : summary}"]`,
    `O["Suggested owner<br/>${label(p.owner)}"]`,
    ...links[p.layout],
  ];
  if (observed) {
    const groups = [];
    for (const status of statuses) {
      const { count, steps } = p.controls[status];
      if (!count) continue;
      const heading = status[0].toUpperCase() + status.slice(1);
      const extra = count > 1 ? ` (+${count - 1})` : "";
      groups.push(`${heading} · ${label(steps[0], 24)}${extra}`);
    }
    lines.push(`E --> R["${groups.join("<br/>")}"]`);
  }
  if (p.decision) {
    lines.push(
      `O -.-> D["Latest owner decision<br/>${p.decision.accepted ? "Accepted" : "Rejected"} · ${label(p.decision.owner, 24)}"]`,
    );
  } else {
    lines.push('O -.-> D["Await owner decision"]');
  }
  return lines;
}

const lines = plan.view === "trace" ? evidenceTrace(plan) : reviewDiagram(plan);
const { svg } = render(lines.join("\n"), {
  idPrefix: plan.view,
  maxWidth: 500,
  fontSize: 15,
  padding: 14,
  limit: 12000,
  maxEdges: 16,
  outputLimit: 220000,
  links: false,
  images: false,
});
process.stdout.write(withWorkspaceColors(svg));
