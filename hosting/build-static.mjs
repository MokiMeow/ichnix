import {
  copyFile,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
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
  "fonts/IBMPlexMono-LICENSE.txt",
  "fonts/IBMPlexMono-Regular.ttf",
  "fonts/InstrumentSans-LICENSE.txt",
  "fonts/InstrumentSans.ttf",
];
const project = resolve(import.meta.dirname, "..");
const output = join(project, "dist");
const staging = await mkdtemp(join(project, ".dist-build-"));
try {
  for (const file of assets) {
    const src = join(project, "public", file);
    const dst = join(staging, file);
    if (!(await lstat(src)).isFile())
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
