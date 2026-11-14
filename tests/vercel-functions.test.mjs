import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { afterEach, test } from "node:test";
import demo from "../api/public-demo.mjs";
import layout from "../api/report-layout.mjs";

const originalFetch = globalThis.fetch;
const managed = [
  "ICHNIX_PUBLIC_ORIGIN",
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
