import { readFileSync } from "node:fs";
import { render } from "pele";
import { withWorkspaceColors } from "../scripts/diagram-theme.mjs";

const input = JSON.parse(readFileSync(0, "utf8"));
const label = (value) =>
  String(value)
    .replace(/[^\p{L}\p{N} ._/-]/gu, " ")
    .trim()
    .slice(0, 80);

function evidenceTrace(events) {
  return [
    "sequenceDiagram",
    "participant C as Customer CI",
    "participant G as Evidence graph",
    ...events
      .slice(0, 64)
      .map((event) => `C->>G: ${label(event.step)} (${label(event.result)})`),
  ];
}

function reviewDiagram(plan) {
  const { passed, failed, inconclusive } = plan.controls;
  const observed = passed + failed + inconclusive;
  const evidence = observed
    ? `${passed} passed / ${failed} failed / ${inconclusive} inconclusive`
    : "Await staging evidence";
  const groups = {
    scope: [
      `G->>S: Service ${label(plan.service)}`,
      `S->>S: ${label(plan.component)} at ${label(plan.revision)}`,
    ],
    evidence: [`G->>C: ${evidence}`],
    owner: [`G->>R: Suggested owner ${label(plan.owner)}`],
  };
  const order = {
    "scope-first": ["scope", "evidence", "owner"],
    "evidence-first": ["evidence", "scope", "owner"],
    "owner-first": ["owner", "scope", "evidence"],
  }[plan.layout];
  return [
    "sequenceDiagram",
    "participant G as Evidence graph",
    "participant S as Service catalog",
    "participant C as CI evidence",
    "participant R as Reviewer",
    `G->>G: Imported case ${label(plan.case)}`,
    ...order.flatMap((group) => groups[group]),
    plan.reviewed
      ? "R->>G: Owner decision recorded"
      : "G->>R: Await owner decision",
  ];
}

const lines = Array.isArray(input)
  ? evidenceTrace(input)
  : reviewDiagram(input);
const { svg } = render(lines.join("\n"), {
  limit: 16000,
  outputLimit: 500000,
  links: false,
  images: false,
});
process.stdout.write(withWorkspaceColors(svg));
