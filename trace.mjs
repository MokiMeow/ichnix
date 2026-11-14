import { readFileSync } from "node:fs";
import { render } from "pele";

const events = JSON.parse(readFileSync(0, "utf8"));
const label = (value) =>
  String(value).replace(/[^\p{L}\p{N} ._/-]/gu, " ").trim().slice(0, 80);
const lines = [
  "sequenceDiagram",
  "participant C as Customer CI",
  "participant G as Evidence graph",
  ...events.slice(0, 64).map((event) =>
    `C->>G: ${label(event.step)} (${label(event.result)})`
  ),
];
process.stdout.write(render(lines.join("\n"), {
  limit: 16000, outputLimit: 500000, links: false, images: false,
}).svg);
