// The routed-host gate, which is the console's access control.
//
// The console has no public route of its own: it answers only requests that
// arrive on an allowed hostname carrying the router credential. Making that
// list configurable is what allows a staging Worker to exist, and it is also
// the change most able to expose the console by accident, so each behaviour
// below is asserted rather than assumed.
//
// These tests exist because the change that introduced CONSOLE_ALLOWED_HOSTNAMES
// shipped without any. The previous suite covered only the hardcoded default.
import assert from "node:assert/strict";
import test from "node:test";

const PROVIDER_HOST = "ratify-maritime-lab.chuksy0x01.chatgpt.site";
const STAGING_HOST = "ratify-maritime-demo-console-staging.chuks-04d.workers.dev";
const TOKEN = "a".repeat(64);

const ctx = { waitUntil() {}, passThroughOnException() {} };
const assets = { fetch: async () => new Response("asset", { status: 200 }) };

async function loadWorker(label) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${label}-${process.pid}-${Date.now()}`);
  return (await import(workerUrl.href)).default;
}

async function request({ host, allowed, token = TOKEN, label = "gate" }) {
  const worker = await loadWorker(label);
  const env = { ASSETS: assets, LABS_ROUTER_TOKEN: TOKEN };
  if (allowed !== undefined) env.CONSOLE_ALLOWED_HOSTNAMES = allowed;
  const headers = token ? { "X-Ratify-Labs-Route": `Bearer ${token}` } : {};
  return worker.fetch(new Request(`https://${host}/maritime`, { headers }), env, ctx);
}

test("without the variable, the existing provider hostname still routes", async () => {
  const response = await request({ host: PROVIDER_HOST, allowed: undefined, label: "default-allow" });
  assert.equal(response.status, 200);
});

test("without the variable, a missing credential is still refused", async () => {
  const response = await request({ host: PROVIDER_HOST, allowed: undefined, token: null, label: "default-deny" });
  assert.equal(response.status, 404);
});

// The operational surprise. The variable REPLACES the default rather than
// extending it, so a deployment that lists only a new hostname stops serving
// the old one. That is correct for a staging Worker and dangerous during a
// production cutover, where both must be listed explicitly.
test("the variable replaces the default rather than adding to it", async () => {
  const refused = await request({ host: PROVIDER_HOST, allowed: STAGING_HOST, label: "replace-refused" });
  assert.equal(refused.status, 404, "the provider hostname must stop routing when it is not listed");

  const allowed = await request({ host: STAGING_HOST, allowed: STAGING_HOST, label: "replace-allowed" });
  assert.equal(allowed.status, 200);
});

test("both hostnames route when both are listed", async () => {
  const list = `${PROVIDER_HOST},${STAGING_HOST}`;
  assert.equal((await request({ host: PROVIDER_HOST, allowed: list, label: "both-a" })).status, 200);
  assert.equal((await request({ host: STAGING_HOST, allowed: list, label: "both-b" })).status, 200);
});

// An empty or whitespace value produces an empty allowlist, so nothing routes.
// Failing closed is the right choice, and it is a silent outage: a deployment
// that sets the variable to an empty string looks configured and serves 404 on
// every request. Asserted so the behaviour is deliberate and cannot drift.
test("an empty allowlist refuses everything rather than falling back", async () => {
  assert.equal((await request({ host: PROVIDER_HOST, allowed: "", label: "empty" })).status, 404);
  assert.equal((await request({ host: PROVIDER_HOST, allowed: "   ", label: "blank" })).status, 404);
});

test("hostnames match case-insensitively and tolerate surrounding spaces", async () => {
  const upper = await request({ host: PROVIDER_HOST.toUpperCase(), allowed: PROVIDER_HOST, label: "case" });
  assert.equal(upper.status, 200);

  const spaced = await request({ host: STAGING_HOST, allowed: ` ${PROVIDER_HOST} , ${STAGING_HOST} `, label: "spaces" });
  assert.equal(spaced.status, 200);
});

// A listed hostname is not a bypass: the credential is still required.
test("a listed hostname still requires the credential", async () => {
  const list = `${PROVIDER_HOST},${STAGING_HOST}`;
  assert.equal((await request({ host: STAGING_HOST, allowed: list, token: null, label: "listed-none" })).status, 404);
  assert.equal((await request({ host: STAGING_HOST, allowed: list, token: "wrong", label: "listed-wrong" })).status, 404);
});

test("an unlisted hostname is refused even with a valid credential", async () => {
  const response = await request({ host: "attacker.example.com", allowed: PROVIDER_HOST, label: "unlisted" });
  assert.equal(response.status, 404);
});
