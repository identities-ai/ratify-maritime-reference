import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/maritime", {
    headers: { accept: "text/html" },
  }), { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } }, {
    waitUntil() {},
    passThroughOnException() {},
  });
}

test("server-renders the authorization lab without starter copy", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Maritime × Ratify — Live Authorization Lab/);
  assert.match(html, /An agent can ask/);
  assert.match(html, /\$420 Seattle electrical repair/);
  assert.match(html, /\$501 Seattle electrical repair/);
  assert.match(html, /Allow plus seven adversarial denials/);
  assert.match(html, /Run full adversarial gate/);
  assert.match(html, /WRONG RESOURCE/);
  assert.match(html, /ALTERED OPERATION/);
  assert.match(html, /EXPIRED/);
  assert.match(html, /REVOKED/);
  assert.match(html, /REPLAY/);
  assert.match(html, /WRONG AGENT/);
  assert.match(html, /What authority does this agent carry/);
  assert.match(html, /custom:work_order:create/);
  assert.match(html, /Seattle warehouse 01/);
  assert.match(html, /Seven days; exact live expiry shown after execution/);
  assert.match(html, /Isolation controls where an agent runs/);
  assert.match(html, /Portable authority/);
  assert.match(html, /From signed permission to protected code/);
  assert.match(html, /The receiver is separately deployed but currently operated by Ratify/);
  assert.match(html, /Live pilot/);
  assert.match(html, /href="https:\/\/maritime\.sh\/"/);
  assert.match(html, /href="https:\/\/ratifyprotocol\.com\/"/);
  assert.match(html, /Open-source pilot implementation/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site|codex-preview/);
  assert.match(html, /src="\/maritime\/ratify-logo\.png"/);
  assert.doesNotMatch(html, /src="\/ratify-logo\.png"/);
  assert.match(html, /RATIFY[\s\S]*LABS/);
  assert.match(html, /rel="icon"[^>]*href="\/maritime\/favicon\.svg"/);
  assert.match(html, /property="og:image"[^>]*content="https:\/\/labs\.ratifyprotocol\.com\/maritime\/og\.jpg"/);

  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /Shared receiver handler count/);
  assert.match(page, /receiver-wide counter shared by every demo visitor/);
  assert.match(page, /index === 4 && result\.decision === "ALLOW"/);
  assert.match(page, /DENY_RESOURCE_MISMATCH/);
  assert.match(page, /DENY_OPERATION_MISMATCH/);
  assert.match(page, /DENY_EXPIRED/);
  assert.match(page, /DENY_REVOKED/);
  assert.match(page, /DENY_REPLAY/);
  assert.match(page, /DENY_SUBJECT_MISMATCH/);
  assert.match(page, /handler_invoked/);
  assert.match(page, /requested_amount_minor/);
  assert.match(page, /authorized_max_amount_minor/);
  assert.match(page, /delegation_expires_at/);
  assert.match(page, /requested_description/);
  assert.match(page, /title: "Demo limit reached"/);
  assert.match(page, /Waiting for the isolated runtimes/);
  assert.match(page, /No automatic retry was attempted/);
  assert.match(page, /title: "Runtime did not return a verified result"/);
  assert.doesNotMatch(page, /<b>Unavailable<\/b>/);
  assert.doesNotMatch(page, /\$750\.00/);
});

test("serves the provider hostname only through the Labs router", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("host-test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) }, LABS_ROUTER_TOKEN: "a".repeat(64) };
  const ctx = { waitUntil() {}, passThroughOnException() {} };

  const provider = await worker.fetch(
    new Request("https://ratify-maritime-lab.chuksy0x01.chatgpt.site/maritime"), env, ctx,
  );
  assert.equal(provider.status, 404);
  assert.equal(provider.headers.get("cache-control"), "no-store");

  const wrong = await worker.fetch(
    new Request("https://ratify-maritime-lab.chuksy0x01.chatgpt.site/maritime", { headers: { "X-Ratify-Labs-Route": "Bearer wrong" } }), env, ctx,
  );
  assert.equal(wrong.status, 404);

  const routed = await worker.fetch(
    new Request("https://ratify-maritime-lab.chuksy0x01.chatgpt.site/maritime", { headers: { "X-Ratify-Labs-Route": `Bearer ${"a".repeat(64)}` } }), env, ctx,
  );
  assert.equal(routed.status, 200);

});
