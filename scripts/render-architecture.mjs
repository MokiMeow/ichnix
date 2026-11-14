import { writeFileSync } from "node:fs";
import { render } from "pele";

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
// Keep the dark connector strokes legible when the README uses a dark theme.
const illustration = svg.replace(
  "<g ",
  '<rect width="100%" height="100%" fill="#fff"/><g ',
);
writeFileSync(
  new URL("../docs/architecture.svg", import.meta.url),
  illustration + "\n",
);
