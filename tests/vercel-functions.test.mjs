import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { afterEach, test } from "node:test";
import demo from "../api/public-demo.mjs";
import layout from "../api/report-layout.mjs";

const originalFetch = globalThis.fetch;
const managed = [
  "ICHNIX_PUBLIC_ORIGIN",
  "ICHNIX_WORKSPACE_ORIGIN",
  "ICHNIX_ORIGIN_SECRET",
  "ICHNIX_LAYOUT_SECRET",
  "ICHNIX_CF_ACCOUNT_ID",
  "ICHNIX_CF_AI_TOKEN",
];
const originalEnv = Object.fromEntries(
  managed.map((key) => [key, process.env[key]]),
);
afterEach(() => {
  globalThis.fetch = originalFetch;
  for (const [key, value] of Object.entries(originalEnv)) {
    if (value == null) delete process.env[key];
    else process.env[key] = value;
  }
});

test("relay only fetches fixture context from an explicitly public session", async () => {
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url.pathname);
    assert.equal(url.origin, process.env.ICHNIX_PUBLIC_ORIGIN);
    assert.equal(options.redirect, "error");
    assert.deepEqual(Object.keys(options.headers), ["accept"]);
    if (url.pathname === "/api/session")
      return Response.json({ public_demo: true, disclosed_case: null });
    return new Response("<svg/>", {
      headers: { "content-type": "image/svg+xml" },
    });
  };
  const req = (path, options) =>
    new Request("https://frontend.example.org" + path, options);
  const trace = await demo.fetch(
    req("/api/public-demo?route=cases/IX-104/diagram.svg", {
      headers: { cookie: "only-local" },
    }),
  );
  assert.equal(trace.status, 200);
  assert.equal(await trace.text(), "<svg/>");
  assert.equal(
    trace.headers.get("content-security-policy").includes("sandbox"),
    true,
  );
  assert.deepEqual(calls, ["/api/session", "/api/cases/IX-104/diagram.svg"]);
  assert.equal(
    (await demo.fetch(req("/api/cases/private-record"))).status,
    404,
  );
  assert.equal(
    (
      await demo.fetch(
        req("/api/cases", { headers: { authorization: "Bearer sample" } }),
      )
    ).status,
    403,
  );
  assert.equal(
    (await demo.fetch(req("/api/cases", { method: "POST" }))).status,
    405,
  );
  assert.equal(calls.length, 2);
  globalThis.fetch = async () => Response.json({ public_demo: false });
  assert.equal((await demo.fetch(req("/api/cases"))).status, 503);
  assert.deepEqual(await (await demo.fetch(req("/api/session"))).json(), {
    public_demo: true,
    disclosed_case: null,
  });
});

test("evaluation publishes aggregate owner checkpoints alongside the six-case curve", async () => {
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  const checkpoint = {
    training: 32,
    heldout: 64,
    scoped: 46,
    pooled: 19,
    catalog: 16,
    unseen_scoped: 4,
  };
  globalThis.fetch = async (url) =>
    Response.json(
      url.pathname === "/api/session"
        ? { public_demo: true }
        : {
            curve: [{ heldout_cases: 6 }],
            owner_curve_64: [{ ...checkpoint, reports: ["private"] }],
          },
    );
  const response = await demo.fetch(
    new Request("https://frontend.example.org/api/evaluation"),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    curve: [{ heldout_cases: 6 }],
    owner_curve_64: [checkpoint],
  });
});

test("public SelfGraph download is limited to approved case facts", async () => {
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  const snapshot = {
    id: "reportsnapshot:7",
    type: "ReportSnapshot",
    name: "IX-108",
    properties: { service: "ledger" },
  };
  const revision = {
    id: "componentrevision:2",
    type: "ComponentRevision",
    name: "r7",
    properties: { component: "audit-store" },
  };
  const observation = {
    id: "controlobservation:4",
    type: "ControlObservation",
    name: "audit",
    properties: { result: "passed" },
  };
  const facts = {
    entities: [snapshot, revision, observation],
    relations: [
      {
        from_entity: snapshot.id,
        type: "ON_REVISION",
        to_entity: revision.id,
        evidence: [{ source_id: snapshot.id }],
      },
      {
        from_entity: snapshot.id,
        type: "HAS_OBSERVATION",
        to_entity: observation.id,
        evidence: [{ source_id: observation.id }],
      },
    ],
  };
  const req = (id, options) =>
    new Request(
      "https://frontend.example.org/api/public-demo?route=cases/" +
        id +
        "/selfgraph.json",
      options,
    );
  const calls = [];
  let body = facts;
  globalThis.fetch = async (url, options) => {
    calls.push(url.pathname);
    assert.deepEqual(Object.keys(options.headers), ["accept"]);
    if (url.pathname === "/api/session")
      return Response.json({ public_demo: true });
    assert.equal(url.pathname, "/api/cases/IX-108/selfgraph.json");
    return Response.json(body, { headers: { "set-cookie": "private-cookie" } });
  };
  assert.equal((await demo.fetch(req("not-public"))).status, 404);
  assert.equal(
    (await demo.fetch(req("IX-108", { method: "POST" }))).status,
    405,
  );
  assert.equal(
    (
      await demo.fetch(
        req("IX-108", { headers: { authorization: "Bearer private" } }),
      )
    ).status,
    403,
  );
  assert.deepEqual(calls, []);
  const response = await demo.fetch(req("IX-108"));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(
    response.headers.get("content-disposition"),
    'attachment; filename="IX-108-selfgraph.json"',
  );
  assert.deepEqual(await response.json(), facts);
  for (const unsafe of [
    {
      ...facts,
      entities: [{ ...snapshot, name: "IX-113" }, revision, observation],
    },
    {
      ...facts,
      entities: [
        snapshot,
        revision,
        { ...observation, properties: { private_note: "internal title" } },
      ],
    },
    { ...facts, private_scope: "hidden" },
    {
      ...facts,
      relations: [
        {
          ...facts.relations[0],
          evidence: [{ source_id: "x", extra: "private" }],
        },
      ],
    },
  ]) {
    body = unsafe;
    assert.equal((await demo.fetch(req("IX-108"))).status, 503);
  }
});

test("layout authenticates before contacting the fixed provider and checks both response formats", async () => {
  const secret = randomBytes(32).toString("hex");
  process.env.ICHNIX_LAYOUT_SECRET = secret;
  process.env.ICHNIX_CF_ACCOUNT_ID = randomBytes(16).toString("hex");
  process.env.ICHNIX_CF_AI_TOKEN = randomBytes(32).toString("hex");
  const ask = (input, auth = secret) =>
    layout.fetch(
      new Request("https://frontend.example.org/api/report-layout", {
        method: "POST",
        headers: {
          authorization: "Bearer " + auth,
          "content-type": "application/json",
        },
        body: JSON.stringify(input),
      }),
    );
  const valid = { source: "hackerone", topic: "data-handling" };
  let provider,
    count = 0;
  globalThis.fetch = async (url, opts) => {
    count++;
    assert.equal(new URL(url).origin, "https://api.cloudflare.com");
    assert.equal(opts.redirect, "error");
    assert.equal(
      opts.headers.authorization,
      "Bearer " + process.env.ICHNIX_CF_AI_TOKEN,
    );
    const data = JSON.parse(opts.body);
    assert.deepEqual(JSON.parse(data.messages[1].content), valid);
    assert.equal(opts.body.includes(secret), false);
    return Response.json(provider);
  };
  assert.equal((await ask(valid, "incorrect")).status, 401);
  assert.equal(
    (await ask({ ...valid, title: "private report text" })).status,
    400,
  );
  assert.equal((await ask({ ...valid, topic: "unsupported" })).status, 400);
  assert.equal(count, 0);
  const result = { layout: "evidence-first" };
  for (const reply of [
    { success: true, result: { response: result } },
    {
      success: true,
      result: { choices: [{ message: { content: JSON.stringify(result) } }] },
    },
  ]) {
    provider = reply;
    assert.deepEqual(await (await ask(valid)).json(), result);
  }
  provider = { success: true, result: { response: { layout: "other" } } };
  assert.equal((await ask(valid)).status, 502);
});

test("organization workspace forwards scoped bearer requests and restricts replies", async () => {
  const workspace = (await import("../api/org-workspace.mjs")).default;
  const org = "https://org.example.org";
  process.env.ICHNIX_WORKSPACE_ORIGIN = org;
  const secret = randomBytes(40).toString("hex");
  process.env.ICHNIX_ORIGIN_SECRET = secret;
  const bearer = "Bearer " + randomBytes(32).toString("hex");
  const orgReq = (route, opts = {}) =>
    new Request(
      "https://frontend.example.org/api/org-workspace?route=" +
        encodeURIComponent(route),
      opts,
    );
  let calls = 0,
    result;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url.origin, org);
    assert.equal(options.redirect, "error");
    assert.equal(options.headers["x-ichnix-origin-secret"], secret);
    assert.equal(options.headers.authorization, bearer);
    assert.equal(options.headers.cookie, undefined);
    assert.equal(options.headers["x-forwarded-host"], undefined);
    assert.equal(url.pathname, "/api/cases/IX-108/reviews");
    assert.equal(options.headers["idempotency-key"], "review-123");
    return result;
  };
  const write = {
    method: "POST",
    headers: {
      authorization: bearer,
      "content-type": "application/json",
      "x-ichnix-origin-secret": "forged",
      cookie: "unwanted",
      "x-forwarded-host": "evil.example",
      "idempotency-key": "review-123",
    },
    body: '{"owner":"ops","accepted":true}',
  };
  result = Response.json(
    { error: "sensitive upstream detail" },
    { status: 403, headers: { "set-cookie": "do-not-forward" } },
  );
  const denial = await workspace.fetch(
    orgReq("org-api/cases/IX-108/reviews", write),
  );
  assert.equal(denial.status, 403);
  assert.equal(denial.headers.has("set-cookie"), false);
  assert.deepEqual(await denial.json(), { error: "request denied" });
  assert.equal(
    (await workspace.fetch(orgReq("org-api/cases/IX-108/reviews"))).status,
    405,
  );
  assert.equal(
    (await workspace.fetch(orgReq("org-api/cases/../reviews", write))).status,
    404,
  );
  assert.equal((await workspace.fetch(orgReq("org-api/cases"))).status, 401);
  assert.equal(
    (await workspace.fetch(orgReq("org-api/handoffs/claim", write))).status,
    400,
  );
  assert.equal(
    (
      await workspace.fetch(
        orgReq("org-api/import/report", {
          ...write,
          body: "x".repeat(1024 * 1024 + 1),
        }),
      )
    ).status,
    413,
  );
  process.env.ICHNIX_PUBLIC_ORIGIN = org;
  assert.equal(
    (await workspace.fetch(orgReq("org-api/cases/IX-108/reviews", write)))
      .status,
    503,
  );
  assert.equal(calls, 1);
});

test("raw signed provider deliveries cross only the dedicated organization route", async () => {
  const workspace = (await import("../api/org-workspace.mjs")).default;
  process.env.ICHNIX_WORKSPACE_ORIGIN = "https://private.example.org";
  process.env.ICHNIX_ORIGIN_SECRET = randomBytes(32).toString("hex");
  const bytes = Buffer.from(' {\n "title" : "München"\n } ');
  for (const [provider, name, value] of [
    ["hackerone", "x-h1-signature", "sha256=" + "2".repeat(64)],
    [
      "bugcrowd",
      "x-bugcrowd-digest",
      "timestamp=2026-11-14T09:00:00Z; sha256=" + "3".repeat(64),
    ],
  ]) {
    globalThis.fetch = async (url, opts) => {
      assert.equal(url.pathname, "/hooks/" + provider);
      assert.deepEqual(opts.body, bytes);
      assert.equal(opts.headers[name], value);
      assert.equal(opts.headers.authorization, undefined);
      assert.equal(opts.headers.cookie, undefined);
      return Response.json({ imported: 1, skipped: 0 });
    };
    const url = "https://frontend.example.org/hooks/" + provider;
    const headers = {
      [name]: value,
      "content-type": "application/json",
      authorization: "Bearer should-stay-here",
      cookie: "strip",
    };
    assert.deepEqual(
      await (
        await workspace.fetch(
          new Request(url, { method: "POST", headers, body: bytes }),
        )
      ).json(),
      { imported: 1, skipped: 0 },
    );
    delete headers[name];
    assert.equal(
      (
        await workspace.fetch(
          new Request(url, { method: "POST", headers, body: bytes }),
        )
      ).status,
      403,
    );
  }
});

test("private case pages forward only a bounded navigation cursor", async () => {
  const workspace = (await import("../api/org-workspace.mjs")).default;
  process.env.ICHNIX_WORKSPACE_ORIGIN = "https://org.example.org";
  process.env.ICHNIX_ORIGIN_SECRET = randomBytes(40).toString("hex");
  const headers = {
    authorization: "Bearer " + randomBytes(32).toString("hex"),
  };
  const request = (query) =>
    new Request(
      "https://frontend.example.org/api/org-workspace?route=org-api%2Fcase-page" +
        query,
      { headers },
    );
  let upstream;
  globalThis.fetch = async (url) => {
    upstream = String(url);
    return Response.json({ cases: [], previous: null, next: null });
  };
  for (const field of ["from", "after", "before"]) {
    assert.equal(
      (await workspace.fetch(request("&" + field + "=IX%3A24"))).status,
      200,
    );
    assert.equal(
      upstream,
      "https://org.example.org/api/case-page?" + field + "=IX%3A24",
    );
  }
  upstream = undefined;
  for (const query of [
    "&before=",
    "&limit=100",
    "&after=A&after=B",
    "&before=A&from=B",
    "&from=%2F",
    "&from=" + "x".repeat(161),
  ]) {
    assert.equal((await workspace.fetch(request(query))).status, 400);
  }
  assert.equal(upstream, undefined);
});

test("hosted identities and leases use only the credentialed private origin", async () => {
  const workspace = (await import("../api/org-workspace.mjs")).default;
  const org = "https://private.example.org";
  process.env.ICHNIX_WORKSPACE_ORIGIN = org;
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  const secret = randomBytes(32).toString("hex");
  process.env.ICHNIX_ORIGIN_SECRET = secret;
  const bearer = "Bearer " + randomBytes(32).toString("hex");
  const token = randomBytes(32).toString("base64url");
  const id = "a".repeat(64);
  const context = {
    report: { id: "IX-24" },
    evidence: [],
    ranked_checks: [],
    recommendations: [],
  };
  const job = {
    id,
    case_id: "IX-24",
    kind: "owner-review",
    service: "fixture",
    component: "fixture",
    revision: "r1",
    state: "leased",
    expires: 2000000000,
    attempts: 1,
    worker: "fixture",
  };
  const identity = { name: "fixture", role: "worker" };
  const request = (path, body, auth = bearer) =>
    new Request("https://frontend.example.org/org-api/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        authorization: auth,
        "content-type": "application/json",
        cookie: "discard",
        "x-ichnix-origin-secret": "discard",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  for (const [path, body, result, extra] of [
    ["auth/status", undefined, identity, { digest: "discard" }],
    ["handoffs/claim", {}, null, null],
    [
      "handoffs/claim",
      { lease_seconds: 90 },
      { ...job, token },
      { token_hash: "discard" },
    ],
    [
      "handoffs/" + id + "/context",
      { token },
      context,
      { related_reports: ["discard"] },
    ],
    [
      "handoffs/" + id + "/finish",
      { token, disposition: "needs-owner-review" },
      {
        ...job,
        state: "complete",
        expires: null,
        disposition: "needs-owner-review",
      },
      { token },
    ],
  ]) {
    let called = false;
    globalThis.fetch = async (url, options) => {
      called = true;
      assert.equal(String(url), org + "/api/" + path);
      assert.equal(options.method, body === undefined ? "GET" : "POST");
      if (body !== undefined) assert.deepEqual(JSON.parse(options.body), body);
      assert.equal(options.headers.authorization, bearer);
      assert.equal(options.headers["x-ichnix-origin-secret"], secret);
      assert.equal(options.headers.cookie, undefined);
      assert.equal(options.redirect, "error");
      return Response.json(result === null ? null : { ...result, ...extra }, {
        headers: { "set-cookie": "discard" },
      });
    };
    const response = await workspace.fetch(request(path, body));
    assert.equal(response.status, 200, path);
    assert.equal(called, true);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.has("set-cookie"), false);
    assert.deepEqual(await response.json(), result);
  }
  let forwarded = 0;
  globalThis.fetch = async () => {
    forwarded++;
    return Response.json({ error: "internal identity data" }, { status: 403 });
  };
  for (const [path, body, expected] of [
    ["auth/status", {}, 405],
    ["auth/status?token=abc", undefined, 400],
    ["handoffs/claim", undefined, 405],
    ["handoffs/claim", [], 400],
    ["handoffs/claim", { lease_seconds: false }, 400],
    ["handoffs/claim", { lease_seconds: 3601 }, 400],
    ["handoffs/claim", { worker: "other" }, 400],
    ["handoffs/claim", { junk: "x".repeat(2048) }, 413],
    ["handoffs/claim?token=abc", {}, 400],
    ["handoffs/000/context", { token }, 404],
    ["handoffs/" + id + "/context", { token, case_id: "elsewhere" }, 400],
    ["handoffs/" + id + "/context", {}, 400],
    ["handoffs/" + id + "/finish", { token, disposition: "accept-owner" }, 400],
    ["handoffs/" + id + "/enqueue", {}, 404],
    ["auth/issue", {}, 404],
  ])
    assert.equal(
      (await workspace.fetch(request(path, body))).status,
      expected,
      path,
    );
  assert.equal(
    (await workspace.fetch(request("handoffs/claim", {}, ""))).status,
    401,
  );
  assert.equal(forwarded, 0);
  const denial = await workspace.fetch(request("handoffs/claim", {}));
  assert.equal(denial.status, 403);
  assert.deepEqual(await denial.json(), { error: "request denied" });
  assert.equal(forwarded, 1);
});

test("the judge relay projects only published fields and checks the selected case", async () => {
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  let record = {
    report: { id: "IX-108", title: "Sample", private_note: "private" },
    evidence: [
      { step: "retention", result: "passed", internal_detail: "private" },
    ],
    private_metadata: "private",
  };
  globalThis.fetch = async (url) =>
    Response.json(
      url.pathname === "/api/session" ? { public_demo: true } : record,
    );
  const request = () =>
    demo.fetch(new Request("https://frontend.example.org/api/cases/IX-108"));
  const response = await request();
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.deepEqual(data.report, { id: "IX-108", title: "Sample" });
  assert.deepEqual(data.evidence, [{ step: "retention", result: "passed" }]);
  assert.equal(Object.hasOwn(data, "private_metadata"), false);
  record.related_reports = [{ id: "not-a-demo-case" }];
  assert.equal((await request()).status, 503);
  record = { report: { id: "IX-104" } };
  assert.equal((await request()).status, 503);
});

test("private Markdown is an authenticated, uncached attachment", async () => {
  const workspace = (await import("../api/org-workspace.mjs")).default;
  process.env.ICHNIX_WORKSPACE_ORIGIN = "https://org.example.org";
  process.env.ICHNIX_ORIGIN_SECRET = "x".repeat(32);
  const url = "https://frontend.example.org/org-api/cases/QA-101/readme.md";
  assert.equal((await workspace.fetch(new Request(url))).status, 401);
  assert.equal(
    (await demo.fetch(new Request(url.replace("/org-api/", "/api/")))).status,
    404,
  );
  const headers = { authorization: "Bearer " + "y".repeat(32) };
  globalThis.fetch = async (target, options) => {
    assert.equal(target.pathname, "/api/cases/QA-101/readme.md");
    assert.equal(options.headers.authorization, headers.authorization);
    assert.equal(options.headers.accept, "text/markdown");
    return new Response("# Report context", {
      headers: { "content-type": "text/markdown; charset=utf-8" },
    });
  };
  const result = await workspace.fetch(new Request(url, { headers }));
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("cache-control"), "no-store");
  assert.equal(
    result.headers.get("content-disposition"),
    'attachment; filename="README.md"',
  );
  assert.equal(await result.text(), "# Report context");
});

test("the guided case is published through the read-only relay on a public origin", async () => {
  process.env.ICHNIX_PUBLIC_ORIGIN = "https://demo.example.org";
  const report = {
    id: "IX-214",
    title: "Audit retention after a staging configuration change",
  };
  globalThis.fetch = async (url) =>
    Response.json(
      url.pathname === "/api/session"
        ? { public_demo: true }
        : { report, private_field: "hidden" },
    );
  const url = "https://frontend.example.org/api/cases/IX-214";
  const result = await demo.fetch(new Request(url));
  assert.equal(result.status, 200);
  assert.deepEqual((await result.json()).report, report);
  assert.equal(
    (await demo.fetch(new Request(url, { method: "POST" }))).status,
    405,
  );
  assert.equal(
    (
      await demo.fetch(
        new Request(url, { headers: { authorization: "Bearer private" } }),
      )
    ).status,
    403,
  );
  assert.equal((await demo.fetch(new Request(url + "/reviews"))).status, 404);
});
