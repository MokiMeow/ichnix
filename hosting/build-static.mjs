import { copyFile, lstat, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const assets = [
  "index.html",
  "docs.html",
  "console.html",
  "app.js",
  "styles.css",
  "docs.css",
  "ichnix-demo.mp4",
  "evidence-graph.png",
  "evidence-atlas.webp",
  "review-detail.webp",
  "fonts/IBMPlexMono-LICENSE.txt",
  "fonts/IBMPlexMono-Regular.ttf",
  "fonts/InstrumentSans-LICENSE.txt",
  "fonts/InstrumentSans.ttf",
];
for (const file of assets) {
  const src = join("public", file);
  const dst = join("dist", file);
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
