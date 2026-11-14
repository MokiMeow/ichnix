// Standalone SVG images do not inherit the workspace's CSS custom properties.
const colors = {
  bg: "#171c1e",
  fg: "#eeede4",
  muted: "#b7bcb9",
  line: "#aac7de",
  surface: "#252d30",
  "surface-alt": "#202729",
  border: "#637174",
  accent: "#aac7de",
};

export function withWorkspaceColors(svg) {
  const palette = Object.entries(colors)
    .map(([name, value]) => `--pele-${name}:${value}`)
    .join(";");
  return svg
    .replace('style="', `style="${palette};`)
    .replace(
      "<g ",
      '<rect width="100%" height="100%" fill="var(--pele-bg)"/><g ',
    );
}
