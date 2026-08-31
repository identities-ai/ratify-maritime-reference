#!/usr/bin/env node
// Verify that every asset the deployed page references actually serves bytes.
//
// A missing path under the console base path is answered with HTTP 200 and an
// empty body rather than a 404, so a status-only check reports success while
// the image renders blank. That is how a broken logo reached production. This
// asserts status, a non-empty body, and a content type consistent with the
// extension, and it fails when the page references no assets at all.

const CONTENT_TYPES = {
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".js": "javascript",
  ".css": "text/css",
  ".woff2": "font/woff2",
  ".ico": "image/",
};

// Kinds that must be present, so discovery finding nothing cannot pass.
const REQUIRED_EXTENSIONS = [".png", ".svg", ".js"];

export function discoverAssets(html, basePath) {
  const pattern = new RegExp(`${basePath}/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+`, "g");
  const paths = (html.match(pattern) ?? []).filter((path) =>
    /\.[A-Za-z0-9]+$/.test(path)
  );
  return [...new Set(paths)].sort();
}

function extensionOf(path) {
  const match = /\.[A-Za-z0-9]+$/.exec(path);
  return match ? match[0].toLowerCase() : "";
}

export async function verifyDeployedAssets({
  baseUrl,
  basePath = "/maritime",
  headers = {},
  fetchImpl = fetch,
}) {
  const problems = [];
  const pageResponse = await fetchImpl(`${baseUrl}${basePath}`, { headers });
  if (pageResponse.status !== 200) {
    return [`page ${basePath} returned ${pageResponse.status}`];
  }
  const html = await pageResponse.text();
  const assets = discoverAssets(html, basePath);

  if (assets.length === 0) {
    return [`page ${basePath} referenced no assets, so nothing was verified`];
  }
  for (const extension of REQUIRED_EXTENSIONS) {
    if (!assets.some((asset) => extensionOf(asset) === extension)) {
      problems.push(`page referenced no ${extension} asset`);
    }
  }

  for (const asset of assets) {
    const response = await fetchImpl(`${baseUrl}${asset}`, { headers });
    if (response.status !== 200) {
      problems.push(`${asset} returned ${response.status}`);
      continue;
    }
    const body = await response.arrayBuffer();
    if (body.byteLength === 0) {
      problems.push(`${asset} returned an empty body with status 200`);
      continue;
    }
    const expected = CONTENT_TYPES[extensionOf(asset)];
    const actual = response.headers.get("content-type") ?? "";
    if (expected && !actual.includes(expected)) {
      problems.push(
        `${asset} served content-type "${actual || "none"}", expected ${expected}`,
      );
    }
  }
  return problems;
}

async function main() {
  const [baseUrl, token] = process.argv.slice(2);
  if (!baseUrl) {
    console.error(
      "usage: verify-deployed-assets.mjs <base-url> [labs-router-token]",
    );
    return 2;
  }
  const headers = { "Cache-Control": "no-cache" };
  if (token) headers["X-Ratify-Labs-Route"] = `Bearer ${token}`;

  const problems = await verifyDeployedAssets({ baseUrl, headers });
  if (problems.length > 0) {
    for (const problem of problems) console.error(`::error::${problem}`);
    return 1;
  }
  console.log("every referenced asset served a non-empty body");
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main();
}
