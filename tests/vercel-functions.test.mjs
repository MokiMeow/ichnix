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
    404,
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
