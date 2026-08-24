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
