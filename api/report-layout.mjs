import { createHash, timingSafeEqual } from "node:crypto";
import { json, readLimited } from "../hosting/http.mjs";

const sources = ["hackerone", "bugcrowd", "local"];
const topics = ["identity", "data-handling", "configuration", "general"];
const layouts = ["scope-first", "evidence-first", "owner-first"];
const schema = {
  type: "object",
  properties: { layout: { type: "string", enum: layouts } },
  required: ["layout"],
  additionalProperties: false,
};

function digest(text) {
  return createHash("sha256").update(text).digest();
}

async function POST(request) {
  const secret = process.env.ICHNIX_LAYOUT_SECRET;
  if (!secret || secret.length < 32)
    return json({ error: "layout unavailable" }, 503);
  if (
    !timingSafeEqual(
      digest(request.headers.get("authorization") || ""),
      digest("Bearer " + secret),
    )
  )
    return json({ error: "unauthorized" }, 401);
  if (
    request.headers.get("content-type")?.split(";")[0].trim() !==
    "application/json"
  )
    return json({ error: "expected JSON" }, 415);
  let input;
  try {
    input = JSON.parse(await readLimited(request, 256));
    if (
      !input ||
      Object.keys(input).length !== 2 ||
      !sources.includes(input.source) ||
      !topics.includes(input.topic)
    )
      throw new Error("invalid categories");
  } catch {
    return json({ error: "invalid categories" }, 400);
  }
  const account = process.env.ICHNIX_CF_ACCOUNT_ID;
  const token = process.env.ICHNIX_CF_AI_TOKEN;
  if (!/^[a-f0-9]{32}$/i.test(account || "") || !token)
    return json({ error: "layout unavailable" }, 503);
  try {
    const upstream = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast`,
      {
        method: "POST",
        redirect: "error",
        signal: AbortSignal.timeout(5000),
        headers: {
          authorization: "Bearer " + token,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          messages: [
            {
              role: "system",
              content:
                "Choose a display order for a report. scope-first emphasizes scope, evidence-first emphasizes recorded observations, owner-first emphasizes routing. Return a single JSON layout.",
            },
            {
              role: "user",
              content: JSON.stringify({
                source: input.source,
                topic: input.topic,
              }),
            },
          ],
          response_format: { type: "json_schema", json_schema: schema },
          max_tokens: 80,
        }),
      },
    );
    if (!upstream.ok) throw new Error("provider unavailable");
    const result = JSON.parse(await readLimited(upstream, 8192));
    if (result.success === false) throw new Error("model failed");
    const resultBody = result.result ?? result;
    const raw =
      resultBody.response ?? resultBody.choices?.[0]?.message?.content;
    const output = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (
      !output ||
      Object.keys(output).length !== 1 ||
      !layouts.includes(output.layout)
    )
      throw new Error("invalid layout");
    return json({ layout: output.layout });
  } catch {
    return json({ error: "layout unavailable" }, 502);
  }
}

export default {
  fetch(request) {
    return request.method === "POST"
      ? POST(request)
      : json({ error: "method not allowed" }, 405);
  },
};
