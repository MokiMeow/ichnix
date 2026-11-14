import { isIP } from "node:net";
import { json, readLimited } from "../hosting/http.mjs";

const caseIDs = ["IX-104", "IX-108", "IX-113", "h1-curl-2236133"];
const allowed = new Set([
  "session",
  "evaluation",
  "acceptance",
  "cases",
  ...caseIDs.flatMap((id) => [
    `cases/${id}`,
    `cases/${id}/trace.svg`,
    `cases/${id}/diagram.svg`,
  ]),
]);

async function GET(request) {
  const url = new URL(request.url);
  const path =
    url.searchParams.get("route") ?? url.pathname.slice("/api/".length);
  if (!allowed.has(path)) return json({ error: "unknown demo route" }, 404);
  if (request.headers.has("authorization"))
    return json({ error: "public demo only" }, 403);
  const unavailable = () =>
    json({ error: "public demo origin unavailable" }, 503);
  try {
    const origin = new URL(process.env.ICHNIX_PUBLIC_ORIGIN);
    if (
      origin.protocol !== "https:" ||
      origin.username ||
      origin.password ||
      origin.search ||
      origin.hash ||
      origin.pathname !== "/" ||
      origin.hostname === url.hostname ||
      !origin.hostname.includes(".") ||
      isIP(origin.hostname) ||
      origin.hostname.endsWith(".localhost")
    )
      throw new Error("invalid demo origin");
    const options = {
      redirect: "error",
      signal: AbortSignal.timeout(3500),
      headers: { accept: "application/json" },
    };
    const mode = await fetch(new URL("/api/session", origin), options);
    if (
      !mode.ok ||
      !mode.headers.get("content-type")?.startsWith("application/json")
    )
      throw new Error("invalid session");
    const info = JSON.parse(await readLimited(mode, 1024));
    if (info.public_demo !== true) throw new Error("private origin");
    if (path === "session")
      return json({
        public_demo: true,
        disclosed_case:
          info.disclosed_case === "h1-curl-2236133"
            ? info.disclosed_case
            : null,
      });
    const svg = path.endsWith(".svg");
    if (svg) options.headers.accept = "image/svg+xml";
    const response = await fetch(new URL("/api/" + path, origin), options);
    if (response.status === 404)
      return json({ error: "record unavailable" }, 404);
    if (
      !response.ok ||
      response.headers.get("content-type")?.split(";")[0] !==
        options.headers.accept
    )
      throw new Error("invalid upstream response");
    const body = await readLimited(response, 1024 * 1024);
    if (!svg) {
      const result = JSON.parse(body);
      if (
        path === "cases" &&
        (!Array.isArray(result) ||
          result.some((row) => !caseIDs.includes(row?.id)))
      )
        throw new Error("unexpected demo case");
      return json(result);
    }
    return new Response(body, {
      headers: {
        "content-type": "image/svg+xml",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "content-security-policy":
          "sandbox; default-src 'none'; style-src 'unsafe-inline'",
      },
    });
  } catch {
    // Keep the hosted console in public mode when its origin is missing or unavailable.
    return path === "session"
      ? json({ public_demo: true, disclosed_case: null })
      : unavailable();
  }
}

export default {
  fetch(request) {
    return request.method === "GET"
      ? GET(request)
      : json({ error: "read-only demo" }, 405);
  },
};
