import assert from "node:assert/strict";
import test from "node:test";

import {
  discoverAssets,
  verifyDeployedAssets,
} from "../scripts/verify-deployed-assets.mjs";

const PAGE = `<!doctype html><html><head>
<link rel="icon" href="/maritime/favicon.svg"/>
<meta property="og:image" content="https://labs.example/maritime/og.jpg"/>
<link rel="stylesheet" href="/maritime/_next/static/css/index.aaa.css"/>
<script src="/maritime/_next/static/chunks/index-bbb.js"></script>
</head><body><img src="/maritime/ratify-logo.png"/></body></html>`;

const BODIES = {
  "/maritime/favicon.svg": ["<svg/>", "image/svg+xml"],
  "/maritime/og.jpg": ["jpegbytes", "image/jpeg"],
  "/maritime/_next/static/css/index.aaa.css": ["body{}", "text/css"],
  "/maritime/_next/static/chunks/index-bbb.js": ["const a=1", "text/javascript"],
  "/maritime/ratify-logo.png": ["pngbytes", "image/png"],
};

function stubFetch(overrides = {}) {
  return async (url) => {
    const path = url.replace("https://console.example", "");
    if (path === "/maritime") {
      return new Response(PAGE, { status: 200 });
    }
    if (path in overrides) return overrides[path];
    const [body, type] = BODIES[path];
    return new Response(body, { status: 200, headers: { "content-type": type } });
  };
}

const verify = (overrides) => verifyDeployedAssets({
  baseUrl: "https://console.example",
  fetchImpl: stubFetch(overrides),
});

test("discovers every referenced asset, including meta image URLs", () => {
  const assets = discoverAssets(PAGE, "/maritime");
  assert.deepEqual(assets, [
    "/maritime/_next/static/chunks/index-bbb.js",
    "/maritime/_next/static/css/index.aaa.css",
    "/maritime/favicon.svg",
    "/maritime/og.jpg",
    "/maritime/ratify-logo.png",
  ]);
});

test("passes when every asset serves bytes", async () => {
  assert.deepEqual(await verify(), []);
});

test("fails on a 200 response with an empty body", async () => {
  // The regression this exists for: the host answers an unmatched path under
  // the base path with an empty 200, so status alone reports success.
  const problems = await verify({
    "/maritime/ratify-logo.png": new Response("", {
      status: 200, headers: { "content-type": "image/png" },
    }),
  });
  assert.deepEqual(problems, [
    "/maritime/ratify-logo.png returned an empty body with status 200",
  ]);
});

test("fails on a missing asset", async () => {
  const problems = await verify({
    "/maritime/favicon.svg": new Response("nope", { status: 404 }),
  });
  assert.deepEqual(problems, ["/maritime/favicon.svg returned 404"]);
});

test("fails when an asset serves the wrong content type", async () => {
  // An HTML fallback served for an image is the other shape this bug takes.
  const problems = await verify({
    "/maritime/ratify-logo.png": new Response("<!doctype html>", {
      status: 200, headers: { "content-type": "text/html" },
    }),
  });
  assert.deepEqual(problems, [
    '/maritime/ratify-logo.png served content-type "text/html", expected image/png',
  ]);
});

test("fails when the page references no assets at all", async () => {
  const problems = await verifyDeployedAssets({
    baseUrl: "https://console.example",
    fetchImpl: async () => new Response("<html><body>bare</body></html>", {
      status: 200,
    }),
  });
  assert.deepEqual(problems, [
    "page /maritime referenced no assets, so nothing was verified",
  ]);
});

test("fails when a required asset kind is absent", async () => {
  const problems = await verifyDeployedAssets({
    baseUrl: "https://console.example",
    fetchImpl: async (url) => url.endsWith("/maritime")
      ? new Response('<img src="/maritime/only.png"/>', { status: 200 })
      : new Response("bytes", {
        status: 200, headers: { "content-type": "image/png" },
      }),
  });
  assert.deepEqual(problems, [
    "page referenced no .svg asset",
    "page referenced no .js asset",
  ]);
});
