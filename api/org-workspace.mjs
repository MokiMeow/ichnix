import { isIP } from "node:net";
import { json, readLimited } from "../hosting/http.mjs";

const signatures = {
  "hooks/hackerone": ["x-h1-signature", /^sha256=[a-f0-9]{64}$/],
  "hooks/bugcrowd": [
    "x-bugcrowd-digest",
    /^timestamp=[^;\s]{1,64};\s*sha256=[a-f0-9]{64}$/,
  ],
};
const errors = new Map([
  [400, "invalid workspace request"],
  [401, "credential required"],
  [403, "request denied"],
  [404, "record unavailable"],
  [405, "method not allowed"],
  [409, "review conflict"],
  [413, "request too large"],
  [415, "expected JSON"],
  [429, "please retry later"],
  [503, "workspace unavailable"],
]);

function upstream(path, request) {
  const origin = new URL(process.env.ICHNIX_WORKSPACE_ORIGIN);
  const secret = process.env.ICHNIX_ORIGIN_SECRET;
  const publicOrigin = process.env.ICHNIX_PUBLIC_ORIGIN;
  const host = origin.hostname;
  if (
    !secret ||
    !/^[\x21-\x7e]{32,512}$/.test(secret) ||
    origin.protocol !== "https:" ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash ||
    origin.pathname !== "/" ||
    !host.includes(".") ||
    isIP(host) ||
    host.endsWith(".localhost") ||
    new URL(request.url).hostname === host ||
    (publicOrigin && new URL(publicOrigin).origin === origin.origin)
  )
    throw new Error("dedicated organization origin required");
  return { url: new URL("/" + path, origin), secret };
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const routes = url.searchParams.getAll("route");
    if (routes.length > 1)
      return json({ error: "unknown workspace route" }, 404);
    const route = routes[0] ?? url.pathname.slice(1);
    const signature = Object.hasOwn(signatures, route)
      ? signatures[route]
      : null;
    const resource =
      /^org-api\/(session|cases|import\/report|cases\/([A-Za-z0-9._:-]{1,160})(?:\/(diagram\.svg|trace\.svg|reviews))?)$/.exec(
        route,
      );
    if ((!resource && !signature) || [".", ".."].includes(resource?.[2]))
      return json({ error: "unknown workspace route" }, 404);
    const write =
      signature ||
      resource?.[1] === "import/report" ||
      resource?.[3] === "reviews";
    if (request.method !== (write ? "POST" : "GET"))
      return json({ error: "method not allowed" }, 405);
    const svg = resource?.[3]?.endsWith(".svg");
    const headers = { accept: svg ? "image/svg+xml" : "application/json" };
    if (signature) {
      const [name, pattern] = signature;
      const digest = request.headers.get(name) ?? "";
      if (!pattern.test(digest) || digest.length > 160)
        return json({ error: "signature required" }, 403);
      headers[name] = digest;
    } else {
      const bearer = request.headers.get("authorization") ?? "";
      if (!/^Bearer [A-Za-z0-9_-]{32,512}$/.test(bearer))
        return json({ error: "credential required" }, 401);
      headers.authorization = bearer;
      const key = request.headers.get("idempotency-key");
      if (resource[3] === "reviews" && key !== null) {
        if (!/^[A-Za-z0-9_.:-]{1,96}$/.test(key))
          return json({ error: "invalid request ID" }, 400);
        headers["idempotency-key"] = key;
      }
    }
    let body;
    if (write) {
      if (
        request.headers.get("content-type")?.split(";")[0].trim() !==
          "application/json" ||
        request.headers.has("content-encoding")
      )
        return json({ error: "expected uncompressed JSON" }, 415);
      const limit = signature ? 1024 * 1024 : 16384;
      try {
        body = await readLimited(request, limit);
      } catch {
        return json({ error: "request too large or empty" }, 413);
      }
      headers["content-type"] = "application/json";
    }
    try {
      const target = upstream(
        signature ? route : "api/" + resource[1],
        request,
      );
      headers["x-ichnix-origin-secret"] = target.secret;
      const response = await fetch(target.url, {
        method: request.method,
        headers,
        body,
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(20000),
      });
      if (response.status !== 200) {
        await response.body?.cancel();
        return json(
          { error: errors.get(response.status) ?? "workspace unavailable" },
          errors.has(response.status) ? response.status : 502,
        );
      }
      if (
        response.headers.get("content-type")?.split(";")[0].trim() !==
        headers.accept
      )
        throw new Error("unexpected response");
      const payload = await readLimited(response, 1024 * 1024);
      if (!svg) return json(JSON.parse(payload));
      const outbound = {
        "content-type": "image/svg+xml",
        "content-security-policy":
          "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      };
      const source = response.headers.get("x-ichnix-layout-source");
      if (["local", "workers-ai", "relay"].includes(source))
        outbound["x-ichnix-layout-source"] = source;
      return new Response(payload, { headers: outbound });
    } catch {
      return json({ error: "workspace unavailable" }, 503);
    }
  },
};
