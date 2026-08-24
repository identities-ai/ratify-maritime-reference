import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", {
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
  assert.match(html, /\$420\.00/);
  assert.match(html, /\$501\.00/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site|codex-preview/);

  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /Shared receiver handler count/);
  assert.match(page, /receiver-wide counter shared by every demo visitor/);
  assert.match(page, /index === 4 && result\.decision === "ALLOW"/);
  assert.match(page, /\$501\.00 USD/);
  assert.doesNotMatch(page, /\$750\.00/);
});

test("rejects the provider hostname and serves only the custom domain", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("host-test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
  const ctx = { waitUntil() {}, passThroughOnException() {} };

  const provider = await worker.fetch(
    new Request("https://ratify-maritime-lab.chuksy0x01.chatgpt.site/"), env, ctx,
  );
  assert.equal(provider.status, 404);
  assert.equal(provider.headers.get("cache-control"), "no-store");

  const custom = await worker.fetch(
    new Request("https://labs.ratifyprotocol.com/"), env, ctx,
  );
  assert.equal(custom.status, 200);
});
