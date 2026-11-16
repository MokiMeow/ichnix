import { isIP } from "node:net";
import { json, readLimited } from "../hosting/http.mjs";

const caseIDs = ["IX-104", "IX-108", "IX-113", "IX-214", "h1-curl-2236133"];
const allowed = new Set([
  "session",
  "evaluation",
  "acceptance",
  "cases",
  ...caseIDs.flatMap((id) => [
    `cases/${id}`,
    `cases/${id}/trace.svg`,
    `cases/${id}/diagram.svg`,
    `cases/${id}/selfgraph.json`,
  ]),
]);

const allowedProperties = new Map([
  ["ReportSnapshot", ["service"]],
  ["ComponentRevision", ["component"]],
  [
    "ControlObservation",
    ["result", "run_id", "source_digest", "source_method"],
  ],
]);

function onlyKeys(value, keys) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).every((key) => keys.includes(key))
  );
}

const reportFields = [
  "id",
  "title",
  "service",
  "revision",
  "component",
  "owner",
];
const caseLists = {
  recommendations: ["owner", "score", "reason"],
  evidence: [
    "step",
    "result",
    "revision",
    "run_id",
    "source_digest",
    "source_method",
  ],
  assessments: ["step", "run_id", "decision", "priority"],
  ranked_checks: ["step", "score", "observations", "reason"],
  review_history: ["owner", "accepted", "actor", "at"],
  related_reports: ["id", "title", "score", "reason", "possible_duplicate"],
  worker_outcomes: ["job_id", "kind", "worker", "disposition", "at"],
};

function publicFields(value, fields) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("invalid demo record");
  const record = {};
  for (const field of fields) {
    if (!Object.hasOwn(value, field)) continue;
    if (
      value[field] !== null &&
      !["number", "string", "boolean"].includes(typeof value[field])
    )
      throw new Error("invalid demo field");
    record[field] = value[field];
  }
  return record;
}

function publicRecord(path, data) {
  if (path === "cases") {
    if (!Array.isArray(data) || data.some((row) => !caseIDs.includes(row?.id)))
      throw new Error("unexpected demo case");
    return data.map((row) => publicFields(row, reportFields));
  }
  if (path === "acceptance") {
    if (data?.case_id !== "h1-curl-2236133")
      throw new Error("unexpected demo receipt");
    return {
      ...publicFields(data, [
        "source",
        "case_id",
        "scope",
        "observed_at",
        "manifest_sha256",
        "source_method",
        "handoff_state",
        "review_recorded",
      ]),
      observations: publicFields(data.observations, [
        "cache_control",
        "content_type_options",
        "referrer_policy",
      ]),
    };
  }
  if (path === "evaluation") {
    return {
      ...publicFields(data, ["dataset", "fixed_catalog_owner_top1"]),
      ...(Array.isArray(data.owner_curve_64) && {
        owner_curve_64: data.owner_curve_64.map((row) =>
          publicFields(row, [
            "training",
            "heldout",
            "scoped",
            "pooled",
            "catalog",
            "unseen_scoped",
          ]),
        ),
      }),
      curve: data.curve.map((row) =>
        publicFields(row, [
          "training_cases",
          "heldout_cases",
          "owner_top1",
          "ci_check_top1",
          "pooled_owner_top1",
          "pooled_ci_check_top1",
          "catalog_owner_top1",
        ]),
      ),
    };
  }
  if (data?.report?.id !== path.slice("cases/".length))
    throw new Error("unexpected demo context");
  const result = { report: publicFields(data.report, reportFields) };
  for (const [name, fields] of Object.entries(caseLists)) {
    const rows = data[name] ?? [];
    if (
      name === "related_reports" &&
      rows.some((row) => !caseIDs.includes(row?.id))
    )
      throw new Error("unexpected related case");
    result[name] = rows.map((row) => publicFields(row, fields));
  }
  return result;
}

function publicFacts(facts, id) {
  if (
    !onlyKeys(facts, ["entities", "relations"]) ||
    !Array.isArray(facts.entities) ||
    !Array.isArray(facts.relations)
  )
    throw new Error("invalid public facts");
  const snapshots = facts.entities.filter((e) => e?.type === "ReportSnapshot");
  if (snapshots.length !== 1 || snapshots[0].name !== id)
    throw new Error("unexpected public snapshot");
  const ids = new Set();
  for (const e of facts.entities) {
    if (
      !onlyKeys(e, ["id", "type", "name", "properties"]) ||
      !allowedProperties.has(e.type) ||
      typeof e.id !== "string" ||
      typeof e.name !== "string" ||
      ids.has(e.id) ||
      !onlyKeys(e.properties, allowedProperties.get(e.type)) ||
      !Object.values(e.properties).every((v) => typeof v === "string")
    )
      throw new Error("unexpected public entity");
    ids.add(e.id);
  }
  for (const r of facts.relations) {
    if (
      !onlyKeys(r, ["from_entity", "type", "to_entity", "evidence"]) ||
      r.from_entity !== snapshots[0].id ||
      !ids.has(r.to_entity) ||
      !["ON_REVISION", "HAS_OBSERVATION"].includes(r.type) ||
      !Array.isArray(r.evidence) ||
      !r.evidence.every(
        (v) => onlyKeys(v, ["source_id"]) && typeof v.source_id === "string",
      )
    )
      throw new Error("unexpected public relation");
  }
  const response = json(facts);
  response.headers.set(
    "content-disposition",
    `attachment; filename="${id}-selfgraph.json"`,
  );
  return response;
}

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
      if (path.endsWith("/selfgraph.json"))
        return publicFacts(result, path.split("/")[1]);
      return json(publicRecord(path, result));
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
