import { writeFileSync } from "node:fs";
import { render } from "pele";
import { withWorkspaceColors } from "./diagram-theme.mjs";

const diagram = `flowchart TB
  P["HackerOne / Bugcrowd"] -->|Signed webhook| I["Private intake"]
  U["Console / CLI upload"] -->|Mapped report| I
  I -->|Report snapshot| G["HydraDB evidence graph"]
  C["Signed staging checks"] --> G
  G <-->|Evidence and assessments| H["Console / CLI reviewer"]
  G <-->|Case context and outcomes| A["Worker API"]
  A <-->|Identity and lease| Q["SQLite"]
  W["Coding agent via MCP"] <-->|Assigned review| A`;

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
  R["New report snapshot"] --> V["Shared component revision"]
  R --> E["Current staging checks"]
  V -->|Find earlier cases| P["Prior report snapshots"]
  P --> C["Prior staging checks"]
  V --> O["Owner decisions"]
  C -->|Rank checks| H["Review context"]
  O -->|Rank owners| H
  E -->|Signed failed check| F["Human assessment"]
  F --> H
  E --> H
  H --> A["Reviewer via console / CLI"]
  H --> W["Worker via MCP"]
  A -->|Accept or reject owner| O
  W --> D["Disposition on leased snapshot"]`;

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
