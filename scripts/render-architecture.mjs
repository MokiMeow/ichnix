import { writeFileSync } from "node:fs";
import { render } from "pele";
import { withWorkspaceColors } from "./diagram-theme.mjs";

const diagram = `flowchart TB
  B["Bounty program"] -->|Mapped report| G["HydraDB evidence graph"]
  C["Customer CI"] -->|Staging observations| G
  H["Reviewer console / CLI"] <-->|Context and decisions| G
  G <-->|Context and outcomes| A["Private handoff API"]
  A <-->|Role and lease checks| Q["SQLite identities and queue"]
  W["Coding agent via MCP"] <-->|One leased review| A`;

const { svg } = render(diagram, {
  idPrefix: "ichnix-arch",
  fontSize: 16,
  padding: 20,
  links: false,
  images: false,
  limit: 4000,
  outputLimit: 40000,
  maxEdges: 12,
});
writeFileSync(
  new URL("../docs/architecture.svg", import.meta.url),
  withWorkspaceColors(svg) + "\n",
);

const review = `flowchart TB
  U["Mapped upload"] -->|Intake token| S["Private intake"]
  P["Program webhook"] -->|Signature + catalog| S
  S --> R["Report snapshot"]
  R --> V["Component revision"]
  C["Signed staging manifest"] -->|Service + revision| V
  V --> E["Recorded controls"]
  E --> H["Review context"]
  V -->|Prior owner decisions| H
  H --> O["Reviewer"]
  H --> W["Leased MCP worker"]
  O -->|Owner decision| D["HydraDB review history"]
  W -->|Disposition| D`;

const workflow = render(review, {
  idPrefix: "ichnix-review",
  fontSize: 15,
  padding: 12,
  links: false,
  images: false,
  limit: 4000,
  outputLimit: 40000,
  maxEdges: 16,
});
writeFileSync(
  new URL("../public/report-review.svg", import.meta.url),
  withWorkspaceColors(workflow.svg) + "\n",
);
