import {
  copyFile,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

const assets = [
  "index.html",
  "docs.html",
  "console.html",
  "app.js",
  "manual.js",
  "styles.css",
  "docs.css",
  "evidence-graph.png",
  "strata.webp",
  "404.html",
  "missing.css",
  "missing-trace.webp",
  "nav.js",
  "strata-mobile.webp",
  "report-review.svg",
  "report-review.webp",
  "fonts/IBMPlexMono-LICENSE.txt",
  "fonts/IBMPlexMono-Regular.woff2",
  "fonts/InstrumentSans-LICENSE.txt",
  "fonts/InstrumentSans.woff2",
];
const project = resolve(import.meta.dirname, "..");
const output = join(project, "dist");
const staging = await mkdtemp(join(project, ".dist-build-"));
try {
  for (const file of assets) {
    const src = join(project, "public", file);
    const dst = join(staging, file);
    if ((await realpath(src)) !== src || !(await lstat(src)).isFile())
      throw new Error("Expected regular public asset: " + file);
    await mkdir(dirname(dst), { recursive: true });
    if (file.endsWith(".html")) {
      const html = await readFile(src, "utf8");
      const root = '<html lang="en">';
      if (!html.includes(root))
        throw new Error("Missing document root in " + file);
      await writeFile(
        dst,
        html.replace(root, '<html lang="en" data-hosted="true">'),
      );
    } else {
      await copyFile(src, dst);
    }
  }
  await rm(output, { recursive: true, force: true });
  await rename(staging, output);
} finally {
  await rm(staging, { recursive: true, force: true });
}
